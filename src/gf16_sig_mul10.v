// SPDX-License-Identifier: Apache-2.0
// gf16_sig_mul10.v - 10x10 unsigned multiplier, 20-bit product, carry-save array.
// Used by gf16_v2_mul for the significand product {1,M} x {1,M}.
// R-SI-1: no arithmetic multiply operator.
//
// Interface and function are unchanged for ALL 2^20 operand pairs (not only operands whose
// top bit is 1): p = a * b.
//
// Structure: row i is the partial product R_i = (b[i] ? a : 0) << i. The rows are added in
// carry-save form: a vector S (sums) and a vector C (carries) with the invariant
//   S + C = R_0 + ... + R_i                 (integers, no truncation)
// maintained by one 3:2 compressor per bit position, using the full-adder identity
//   x + y + z = (x ^ y ^ z) + 2 * maj(x, y, z)  for single bits:
//   S' = S ^ C ^ R,   C' = maj(S, C, R) << 1.
// No carry is lost by keeping 20 bits: a carry out of bit 19 would need two of the three
// operands to have bit 19 set, so S + C + R >= 2^20, but every partial sum is at most
// a*b <= 1023*1023 < 2^20. After the last row S + C = a*b; one explicit ripple full-adder
// chain (the only carry-propagate addition) produces p. Bits that are constant zero (the
// low part of C, the high part of the early rows) are removed by constant propagation.
//
// Test coverage: test/tb_sig_mul10.v compares all 2^20 operand pairs with an integer product
// (test code, a multiply operator is allowed there). That run is a check to be executed
// (locally or in CI); this comment records no result of it.
`default_nettype none
module gf16_sig_mul10 (
    input  wire [9:0]  a,
    input  wire [9:0]  b,
    output wire [19:0] p
);

    reg [19:0] s;        // carry-save sum vector
    reg [19:0] c;        // carry-save carry vector
    reg [19:0] r;        // partial-product row
    reg [19:0] sn;
    reg [19:0] cn;
    reg [19:0] res;
    reg        k;        // ripple carry of the final addition
    integer i, j;

    always @* begin
        s = 20'd0;
        c = 20'd0;
        for (i = 0; i < 10; i = i + 1) begin
            r  = {10'd0, (a & {10{b[i]}})} << i;
            sn = s ^ c ^ r;
            cn = ((s & c) | (s & r) | (c & r)) << 1;
            s  = sn;
            c  = cn;
        end
        k = 1'b0;
        for (j = 0; j < 20; j = j + 1) begin
            res[j] = s[j] ^ c[j] ^ k;
            k      = (s[j] & c[j]) | (s[j] & k) | (c[j] & k);
        end
    end

    assign p = res;

endmodule
`default_nettype wire
