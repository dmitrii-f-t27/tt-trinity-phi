// SPDX-License-Identifier: Apache-2.0
// gf16_sig_mul10.v - 10x10 unsigned shift-and-add multiplier, 20-bit product.
// Used by gf16_v2_mul for the significand product {1,M} x {1,M}.
// R-SI-1: no arithmetic multiply operator; the partial products are added by shifts.
// Test coverage: test/tb_sig_mul10.v compares all 2^20 operand pairs with an integer product
// (test code, a multiply operator is allowed there). That run is a check to be executed
// (locally or in CI); this comment records no result of it.
`default_nettype none
module gf16_sig_mul10 (
    input  wire [9:0]  a,
    input  wire [9:0]  b,
    output reg  [19:0] p
);

    integer i;

    always @* begin
        p = 20'd0;
        for (i = 0; i < 10; i = i + 1) begin
            if (b[i]) p = p + ({10'd0, a} << i);
        end
    end

endmodule
`default_nettype wire
