// SPDX-License-Identifier: Apache-2.0
// gf16_v2_add.v - GoldenFloat16 adder [S(1) | E(6) | M(9)], bias 31, EXACT round-to-
// nearest-even of the exact sum.
//
// SKY26d contract (specs/numeric/gf16_dot4_sky26d.t27, issue #100). For finite nonzero
// operands the exact sum is normalised into an unbounded 1.9 binade, rounded to nearest
// with a tie going to the EVEN mantissa, with carry into the next binade; only then the
// range rules apply: biased exponent >= 63 -> signed Inf; < 0, or 0 with M = 0 -> +0.
// Every zero result is +0 (exact cancellation, zero + zero, underflow). zero + x -> x
// unchanged. Any NaN operand and Inf + (-Inf) give NaN 0xFE01; Inf + Inf of equal sign
// and Inf + finite give that Inf.
//
// History: the file name is kept from the archived baseline (617c0fc). The baseline
// adder rounded faithfully (<= 1 ULP) with a truncating alignment; for SKY26d the adder
// is replaced by this exact one. The baseline is frozen and is NOT claimed unchanged.
//
// Method (13-bit significands with 3 extra low bits, 14-bit sum):
//   * the operand with the larger magnitude {E,M} is the high one, the other the low one;
//   * the low significand is shifted right by d = E_big - E_small; every bit shifted
//     out is OR-ed into bit 0 (jam). For d <= 3 nothing is lost. For d >= 4 an
//     effective subtraction moves the leading bit by at most one place, so the jammed
//     bit only ever sits below the rounding position and the decision on guard and
//     "rest is nonzero" is exactly that of the true difference;
//   * after the add or subtract the sum is normalised by a leading-zero count and rounded.
`default_nettype none
module gf16_v2_add (
    input  wire [15:0] a,
    input  wire [15:0] b,
    output reg  [15:0] result
);

    wire        sa = a[15];
    wire [5:0]  ea = a[14:9];
    wire [8:0]  ma = a[8:0];
    wire        sb = b[15];
    wire [5:0]  eb = b[14:9];
    wire [8:0]  mb = b[8:0];

    wire zero_a = (ea == 6'd0)  && (ma == 9'd0);
    wire zero_b = (eb == 6'd0)  && (mb == 9'd0);
    wire inf_a  = (ea == 6'd63) && (ma == 9'd0);
    wire inf_b  = (eb == 6'd63) && (mb == 9'd0);
    wire nan_a  = (ea == 6'd63) && (ma != 9'd0);
    wire nan_b  = (eb == 6'd63) && (mb != 9'd0);

    // magnitude order of the {E,M} fields (meaningful for finite operands)
    wire        a_big = (a[14:0] >= b[14:0]);
    wire [15:0] op_hi = a_big ? a : b;
    wire [15:0] op_lo = a_big ? b : a;

    wire        sgn_big = op_hi[15];
    wire        sgn_sml = op_lo[15];
    wire [5:0]  e_big   = op_hi[14:9];
    wire [5:0]  e_sml   = op_lo[14:9];
    wire [9:0]  sig_big = {1'b1, op_hi[8:0]};
    wire [9:0]  sig_sml = {1'b1, op_lo[8:0]};

    wire [5:0]  dif  = e_big - e_sml;
    wire [12:0] bext = {sig_big, 3'b000};
    wire [12:0] sext = {sig_sml, 3'b000};

    // right shift with sticky; shifts of 13 or more move every bit out
    wire [3:0]  dsh  = (dif > 6'd13) ? 4'd13 : dif[3:0];
    wire [25:0] sx   = {sext, 13'd0} >> dsh;
    wire [12:0] shf  = sx[25:13];
    wire        stk  = |sx[12:0];
    wire [12:0] smj  = {shf[12:1], shf[0] | stk};

    wire        eff_sub = sgn_big ^ sgn_sml;
    wire [13:0] tsum = eff_sub ? ({1'b0, bext} - {1'b0, smj})
                               : ({1'b0, bext} + {1'b0, smj});

    function [3:0] lzc14;
        input [13:0] x;
        begin
            casez (x)
                14'b1?????????????: lzc14 = 4'd0;
                14'b01????????????: lzc14 = 4'd1;
                14'b001???????????: lzc14 = 4'd2;
                14'b0001??????????: lzc14 = 4'd3;
                14'b00001?????????: lzc14 = 4'd4;
                14'b000001????????: lzc14 = 4'd5;
                14'b0000001???????: lzc14 = 4'd6;
                14'b00000001??????: lzc14 = 4'd7;
                14'b000000001?????: lzc14 = 4'd8;
                14'b0000000001????: lzc14 = 4'd9;
                14'b00000000001???: lzc14 = 4'd10;
                14'b000000000001??: lzc14 = 4'd11;
                14'b0000000000001?: lzc14 = 4'd12;
                14'b00000000000001: lzc14 = 4'd13;
                default:            lzc14 = 4'd14;
            endcase
        end
    endfunction

    wire [3:0]  lz   = lzc14(tsum);
    wire [13:0] norm = tsum << lz;               // leading one at bit 13 (tsum != 0)
    wire [9:0]  sigk = norm[13:4];
    wire        g    = norm[3];
    wire        rest = |norm[2:0];
    wire        lsb  = sigk[0];
    wire        up   = g & (rest | lsb);          // tie goes to the even mantissa
    wire [10:0] sig_r = {1'b0, sigk} + {10'd0, up};
    wire        carry = sig_r[10];
    wire [8:0]  mant  = carry ? 9'd0 : sig_r[8:0];

    // biased exponent: e_big + 1 - lz + carry, 8-bit two's complement, range -12 .. 64
    wire [7:0] eres = {2'b00, e_big} + 8'd1 + {7'd0, carry} - {4'd0, lz};
    wire       eneg = eres[7];

    always @* begin
        if (nan_a || nan_b)
            result = 16'hFE01;
        else if (inf_a && inf_b)
            result = (sa == sb) ? a : 16'hFE01;
        else if (inf_a)
            result = a;
        else if (inf_b)
            result = b;
        else if (zero_a && zero_b)
            result = 16'h0000;
        else if (zero_a)
            result = b;
        else if (zero_b)
            result = a;
        else if (tsum == 14'd0)
            result = 16'h0000;                     // exact cancellation
        else if (eneg || ((eres == 8'd0) && (mant == 9'd0)))
            result = 16'h0000;                     // underflow and the 2^-31 gap flush to +0
        else if (eres[6:0] >= 7'd63)
            result = {sgn_big, 6'd63, 9'd0};       // overflow
        else
            result = {sgn_big, eres[5:0], mant};
    end

endmodule
`default_nettype wire
