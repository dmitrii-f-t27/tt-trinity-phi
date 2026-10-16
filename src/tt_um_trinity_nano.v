// SPDX-License-Identifier: Apache-2.0
// tt_um_trinity_nano.v - Tiny Tapeout SKY26d, 1x1 tile: pin-loadable GF16 DOT4.
// Contract: specs/numeric/gf16_dot4_sky26d.t27 (issue #100). Names are kept from the
// original project (author: Dmitrii Vasilev); this top is rewritten by Dmitrii Fedorov.
//
// r = DOT4(A0..A3, B0..B3), GF16 = S1 E6 M9, bias 31:
//   p_k = MUL(A_k, B_k); acc = p_0; acc = ADD(acc, p_1); acc = ADD(acc, p_2);
//   r = ADD(acc, p_3). Each operation is rounded to GF16 at once. One shared MUL and
//   one shared ADD, sequenced by a counter, exactly 8 enabled clocks from GO to DONE.
//
// Pins (uio is INPUT ONLY, uio_oe = 0x00, uio_out = 0x00 in every state):
//   ui_in[7:0]    data byte for a write; in read select 5 only ui_in[3:0] is used
//   uio_in[0]     WR    rising edge writes ui_in into cell[PTR], PTR = PTR + 1 (mod 16)
//   uio_in[1]     GO    rising edge starts DOT4 (PTR is reset to 0)
//   uio_in[4:2]   RSEL  0 STATUS {PTR[3:0], 0, ERR, DONE, BUSY}
//                       1 RES_LO, 2 RES_HI (0x00 while BUSY)
//                       3 SIG_LO 0xC0, 4 SIG_HI 0x47 (constant signature, not a result)
//                       5 READBACK cell[ui_in[3:0]], 6 and 7 read 0x00
//   uio_in[7:5]   reserved, ignored
//   ena = 0 freezes all state including the edge history; rst_n is asynchronous, active low.
// Memory: 16 byte cells, little endian words, A0..A3 at 0..7, B0..B3 at 8..15, reset to 0.
// WR and GO rising in the same idle clock: WR is accepted, GO is ignored, no flag.
// While BUSY, WR and GO rising edges are ignored and set the sticky ERR (cleared by an
// accepted GO and by reset). An accepted WR clears DONE and leaves the result register.
`default_nettype none
module tt_um_trinity_nano (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

    reg [127:0] mem;
    reg [3:0]   ptr;
    reg [2:0]   step;
    reg         busy;
    reg         done;
    reg         err;
    reg         wr_q;
    reg         go_q;
    reg [15:0]  prod;
    reg [15:0]  acc;

    wire wr_rise = uio_in[0] & ~wr_q;
    wire go_rise = uio_in[1] & ~go_q;

    // operands of lane step[2:1]: A_k at bit offset 16k, B_k at bit offset 64 + 16k
    wire [1:0]  lane = step[2:1];
    wire [15:0] opa  = mem[{1'b0, lane, 4'b0000} +: 16];
    wire [15:0] opb  = mem[{1'b1, lane, 4'b0000} +: 16];

    wire [15:0] mul_y;
    wire [15:0] add_y;
    gf16_v2_mul u_mul (.a(opa), .b(opb), .result(mul_y));
    gf16_v2_add u_add (.a(acc), .b(prod), .result(add_y));

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mem  <= 128'd0;
            ptr  <= 4'd0;
            step <= 3'd0;
            busy <= 1'b0;
            done <= 1'b0;
            err  <= 1'b0;
            wr_q <= 1'b0;
            go_q <= 1'b0;
            prod <= 16'd0;
            acc  <= 16'd0;
        end else if (ena) begin
            wr_q <= uio_in[0];
            go_q <= uio_in[1];
            if (busy) begin
                if (wr_rise | go_rise) err <= 1'b1;
                step <= step + 3'd1;
                case (step)
                    3'd0, 3'd2, 3'd4, 3'd6: prod <= mul_y;
                    3'd1:                   acc  <= prod;
                    default:                acc  <= add_y;
                endcase
                if (step == 3'd7) begin
                    busy <= 1'b0;
                    done <= 1'b1;
                end
            end else if (wr_rise) begin
                mem[{ptr, 3'b000} +: 8] <= ui_in;
                ptr  <= ptr + 4'd1;
                done <= 1'b0;
            end else if (go_rise) begin
                ptr  <= 4'd0;
                done <= 1'b0;
                err  <= 1'b0;
                busy <= 1'b1;
                step <= 3'd0;
            end
        end
    end

    wire [7:0] status = {ptr, 1'b0, err, done, busy};
    reg  [7:0] rd;

    always @* begin
        case (uio_in[4:2])
            3'd0:    rd = status;
            3'd1:    rd = busy ? 8'h00 : acc[7:0];
            3'd2:    rd = busy ? 8'h00 : acc[15:8];
            3'd3:    rd = 8'hC0;
            3'd4:    rd = 8'h47;
            3'd5:    rd = mem[{ui_in[3:0], 3'b000} +: 8];
            default: rd = 8'h00;
        endcase
    end

    assign uo_out  = rd;
    assign uio_out = 8'h00;
    assign uio_oe  = 8'h00;

    wire _unused = &{1'b0, uio_in[7:5]};

endmodule
`default_nettype wire
