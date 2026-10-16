// SPDX-License-Identifier: Apache-2.0
// gf16_v2_mul.v - GoldenFloat16 multiplier [S(1) | E(6) | M(9)], bias 31.
// value = (-1)^S (1 + M/2^9) 2^(E-31); E = 0 with M != 0 is a NORMAL number, E = 0
// with M = 0 is zero, E = 63 is Inf (M = 0) or NaN (M != 0).
//
// SKY26d contract (specs/numeric/gf16_dot4_sky26d.t27, issue #100):
//   - the exact product is normalised into an unbounded 1.9 binade, rounded to nearest
//     with a tie going TOWARD ZERO (round up only if the discarded part is > 1/2),
//     with carry into the next binade; only THEN the range rules apply:
//     biased exponent >= 63 -> signed Inf; < 0, or 0 with M = 0 -> +0.
//   - every zero result is +0 (the sign of a zero product is dropped).
//   - any NaN operand, and Inf * 0, give NaN 0xFE01. Inf * nonzero -> Inf, sign S_a ^ S_b.
// History: the file name is kept from the archived baseline (617c0fc). The rounding
// mode and the zero/underflow/overflow policy are unchanged from that baseline; the
// only change is that the significand product is built by gf16_sig_mul10 (shift-and-add)
// instead of an arithmetic multiply operator (R-SI-1).
`default_nettype none
module gf16_v2_mul (
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

    wire rs = sa ^ sb;

    // significand product, 20 bits: 2^18 <= prod < 2^20
    wire [19:0] prod;
    gf16_sig_mul10 u_sig (.a({1'b1, ma}), .b({1'b1, mb}), .p(prod));

    // prod[19] set: value in [2,4) -> keep prod[19:10], discard 10 bits (half = 512)
    // prod[19] clear: value in [1,2) -> keep prod[18:9], discard 9 bits (half = 256)
    wire        top  = prod[19];
    wire [9:0]  sig  = top ? prod[19:10] : prod[18:9];
    wire [9:0]  rem  = top ? prod[9:0]   : {1'b0, prod[8:0]};
    wire [9:0]  half = top ? 10'd512     : 10'd256;
    wire        up   = (rem > half);              // tie goes toward zero
    wire [10:0] sig_r = {1'b0, sig} + {10'd0, up};
    wire        carry = sig_r[10];
    wire [8:0]  mant  = carry ? 9'd0 : sig_r[8:0];

    // biased exponent of the result, 8-bit two's complement, range -31 .. 95
    wire [7:0] esum = {2'b00, ea} + {2'b00, eb};
    wire [7:0] eres = esum + {7'd0, top} + {7'd0, carry} - 8'd31;
    wire       eneg = eres[7];

    always @* begin
        if (nan_a || nan_b || (inf_a && zero_b) || (inf_b && zero_a))
            result = 16'hFE01;
        else if (inf_a || inf_b)
            result = {rs, 6'd63, 9'd0};
        else if (zero_a || zero_b)
            result = 16'h0000;
        else if (eneg || ((eres == 8'd0) && (mant == 9'd0)))
            result = 16'h0000;                     // underflow and the 2^-31 gap flush to +0
        else if (eres[6:0] >= 7'd63)
            result = {rs, 6'd63, 9'd0};            // overflow
        else
            result = {rs, eres[5:0], mant};
    end

endmodule
`default_nettype wire
