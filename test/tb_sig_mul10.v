// SPDX-License-Identifier: Apache-2.0
// Exhaustive check of the shift-and-add 10x10 multiplier (all 2^20 operand pairs, which
// includes all 2^18 significand pairs {1,Ma} x {1,Mb}) against the integer product.
// This is TEST code: the multiply operator is allowed here, it is the reference.
`timescale 1ns/1ps
module tb_sig_mul10;
    reg  [9:0]  a;
    reg  [9:0]  b;
    wire [19:0] p;
    reg  [19:0] refp;
    integer i, j, bad, n;

    gf16_sig_mul10 dut (.a(a), .b(b), .p(p));

    initial begin
        bad = 0;
        n = 0;
        for (i = 0; i < 1024; i = i + 1) begin
            for (j = 0; j < 1024; j = j + 1) begin
                a = i;
                b = j;
                #1;
                refp = a * b;
                n = n + 1;
                if (p !== refp) begin
                    bad = bad + 1;
                    if (bad <= 10) $display("MISMATCH a=%0d b=%0d p=%0d ref=%0d", a, b, p, refp);
                end
            end
        end
        $display("SIGMUL10_RESULT N=%0d MISMATCH=%0d", n, bad);
        $finish;
    end
endmodule
