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
// History: the file name is kept from the archived baseline (617c0fc). The baseline adder
// rounded faithfully (<= 1 ULP) with a truncating alignment; for SKY26d the adder is
// replaced by this exact one. The baseline is frozen and is NOT claimed unchanged.
//
// Method (13-bit significands with 3 extra low bits, 14-bit sum):
//   * the operand with the larger magnitude {E,M} is the high one, the other the low one;
//   * the low significand is shifted right by d = E_hi - E_lo in stages of 1, 2, 4 and 8
//     positions (d <= 12); every bit shifted out of a stage is OR-ed into one sticky bit,
//     which is then OR-ed into bit 0 of the result (jam). For d >= 13 the whole low
//     significand leaves the 13-bit window and the aligned operand is exactly 1 (jam of a
//     nonzero value). For d <= 3 nothing nonzero is lost. For d >= 4 an effective
//     subtraction moves the leading bit by at most one place, so the jammed bit only ever
//     sits below the rounding position and the decision on guard and "rest is nonzero" is
//     exactly that of the true difference;
//   * ONE 14-bit adder does both operations: for an effective subtraction the aligned
//     operand is inverted and a carry-in of 1 is added (two's complement); both come from a
//     single 15-bit addition whose low bit supplies the carry-in;
//   * after the add the sum is normalised by a leading-zero count and rounded.
//
// Algebra (checked by test/unit_sweeps.py against the exact rational oracle, not asserted here):
//   staged shift: the stages move by 1, 2, 4, 8 exactly when bits 0..3 of d are set, so the total
//   shift is d (d <= 12) and the OR of the bits dropped stage by stage is the OR of the d
//   lowest bits of the original significand, the same set as the single-shift form;
//   add/sub: for X in [0, 2^13), Y in [0, 2^13): X - Y (X >= Y) = X + (~Y) + 1 modulo 2^14 with
//   ~Y taken over 14 bits, and the 14-bit result equals the true difference because X >= Y;
//   X + Y < 2^14 needs no wrap. {X,1} + {Y',c} with c = eff_sub and Y' = Y xor eff_sub
//   gives (X + Y' + c) in bits [14:1] because bit 0 contributes 1 + c, whose carry is c.
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
    wire        a_big   = (a[14:0] >= b[14:0]);
    wire [14:0] mg_hi   = a_big ? a[14:0] : b[14:0];
    wire [14:0] mg_lo   = a_big ? b[14:0] : a[14:0];
    wire        sgn_big = a_big ? sa : sb;       // sign of the larger-magnitude operand
    wire        eff_sub = sa ^ sb;               // effective subtraction (order independent)

    wire [5:0]  e_big   = mg_hi[14:9];
    wire [5:0]  e_sml   = mg_lo[14:9];
    wire [9:0]  sig_big = {1'b1, mg_hi[8:0]};
    wire [9:0]  sig_sml = {1'b1, mg_lo[8:0]};

    wire [5:0]  dif  = e_big - e_sml;
    wire [12:0] bext = {sig_big, 3'b000};
    wire [12:0] sext = {sig_sml, 3'b000};

    // alignment: d >= 13 -> the aligned operand is exactly 1 (everything jammed)
    wire        far  = (|dif[5:4]) | (dif[3] & dif[2] & (dif[1] | dif[0]));

    // right shift by d (d <= 12) in stages 1, 2, 4, 8 with a sticky bit
    wire [12:0] v1 = dif[0] ? {1'b0,  sext[12:1]} : sext;
    wire        k1 = dif[0] & sext[0];
    wire [12:0] v2 = dif[1] ? {2'b00, v1[12:2]}   : v1;
    wire        k2 = k1 | (dif[1] & (|v1[1:0]));
    wire [12:0] v3 = dif[2] ? {4'b0,  v2[12:4]}   : v2;
    wire        k3 = k2 | (dif[2] & (|v2[3:0]));
    wire [12:0] v4 = dif[3] ? {8'b0,  v3[12:8]}   : v3;
    wire        k4 = k3 | (dif[3] & (|v3[7:0]));
    wire [12:0] smj_near = {v4[12:1], v4[0] | k4};
    wire [12:0] smj      = far ? 13'd1 : smj_near;

    // ONE adder for add and subtract: bext + (smj xor eff_sub) + eff_sub, 14 bits
    wire [13:0] smj_x  = {1'b0, smj} ^ {14{eff_sub}};
    wire [14:0] sum15  = {1'b0, bext, 1'b1} + {smj_x, eff_sub};
    wire [13:0] tsum   = sum15[14:1];

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
