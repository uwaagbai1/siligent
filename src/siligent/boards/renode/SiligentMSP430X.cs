// Siligent's MSP430X core for Renode 1.17. It is Renode's own core with two
// changes, loaded by the backend with `include` before the platform file:
//
// 1. Cycle costs. The stock core moves virtual time on by one step per
//    instruction, so every instruction looked like one cycle. Here each one is
//    charged what TI's manual says it takes on the CPUX (SLAU367P, tables 4-8 to
//    4-19), so the timers, and the cycle counts tests report, follow the real chip.
// 2. Multi-bit rotates. The stock RRCM, RRAM, RLAM and RRUM keep bits above the
//    operand width and lose the carry flag, which breaks TI's software floating
//    point. They are done here instead.
//
// Known gaps: interrupt entry is detected from GIE being cleared, and after a
// rotate a pending interrupt waits one more instruction.

using System;
using Antmicro.Renode.Core;

namespace Antmicro.Renode.Peripherals.CPU
{
    public class SiligentMSP430X : MSP430X
    {
        public SiligentMSP430X(IMachine machine, string cpuType) : base(machine, cpuType)
        {
        }

        // Cycles charged since the last reset.
        public ulong Cycles { get; private set; }

        public override void Reset()
        {
            base.Reset();
            Cycles = 0;
            owed = 0;
        }

        public override ExecutionResult ExecuteInstructions(ulong budget, out ulong executed)
        {
            // Renode hands out time as a number of "instructions". One here is one
            // cycle; cycles left over from the last instruction are paid first, so
            // the budget is never exceeded.
            executed = 0;
            while(executed < budget)
            {
                if(owed > 0)
                {
                    var paid = Math.Min(owed, budget - executed);
                    owed -= paid;
                    executed += paid;
                    continue;
                }

                var pc = (uint)PC.RawValue;
                var word = Word(pc);
                ulong cost;
                if(IsRotate(word))
                {
                    Rotate(word);
                    cost = (ulong)RotateCount(word);
                }
                else
                {
                    cost = (ulong)CycleCost(pc);
                    var gieBefore = (GetRegister(2).RawValue & Gie) != 0;
                    var result = base.ExecuteInstructions(1, out var steps);
                    if(steps == 0)
                    {
                        return result;
                    }
                    if(gieBefore && (GetRegister(2).RawValue & Gie) == 0 && !WritesStatusRegister(pc))
                    {
                        cost += 6; // the core took an interrupt after the instruction
                    }
                    if(result != ExecutionResult.Ok)
                    {
                        Charge(cost);
                        return result;
                    }
                }
                Charge(cost);
            }
            return ExecutionResult.Ok;
        }

        // Cycles the instruction at this address takes, from TI's tables.
        public int CycleCost(ulong address)
        {
            var pc = (uint)address;
            var w = Word(pc);
            if(w >= 0x1800 && w < 0x2000)
            {
                return ExtendedCost(w, Word(pc + 2));
            }
            switch(w >> 12)
            {
                case 0:
                    return AddressCost(w);
                case 1:
                    if(w < 0x1300) return FormatIICost(w);
                    if(w == 0x1300) return 5; // RETI
                    if(w < 0x1400) return CallaCost(w);
                    var n = ((w >> 4) & 0xF) + 1; // PUSHM / POPM
                    return (w & 0x0100) == 0 ? 2 + 2 * n : 2 + n;
                case 2:
                case 3:
                    return 2; // jumps, taken or not
                default:
                    return FormatICost(w);
            }
        }

        private void Charge(ulong cost)
        {
            Cycles += cost;
            owed += cost;
        }

        private ushort Word(uint address)
        {
            return machine.SystemBus.ReadWord(address);
        }

        // Addressing modes, after the constant generator (R2/R3) is taken into
        // account: #0, #1, #2, #4, #8 and #-1 cost the same as a register.
        private enum Mode { Register, Indirect, IndirectIncrement, Immediate, Memory, Absolute }

        private static Mode SourceMode(int register, int As)
        {
            if(register == 3) return Mode.Register;
            if(register == 2) return As == 1 ? Mode.Absolute : Mode.Register;
            if(register == 0 && As == 3) return Mode.Immediate;
            switch(As)
            {
                case 0: return Mode.Register;
                case 1: return Mode.Memory;
                case 2: return Mode.Indirect;
                default: return Mode.IndirectIncrement;
            }
        }

        // Table 4-10, MSP430 Format I. Columns: to a register, to PC, to memory.
        private static readonly int[,] FormatI = {
            { 1, 3, 4 }, // Rn
            { 2, 4, 5 }, // @Rn
            { 2, 4, 5 }, // @Rn+
            { 2, 3, 5 }, // #N
            { 3, 5, 6 }, // x(Rn), EDE, &EDE
        };

        // Table 4-18, MSP430X Format I, .B/.W and .A.
        private static readonly int[,] FormatIX = {
            { 2, 4, 5 }, { 3, 5, 6 }, { 3, 5, 6 }, { 3, 4, 6 }, { 4, 6, 7 },
        };
        private static readonly int[,] FormatIXA = {
            { 2, 4, 7 }, { 4, 6, 9 }, { 4, 6, 9 }, { 3, 4, 8 }, { 5, 7, 10 },
        };

        private static int Row(Mode m)
        {
            return m == Mode.Absolute ? 4 : Math.Min((int)m, 4);
        }

        private static int Column(int instr)
        {
            if(((instr >> 7) & 1) != 0) return 2;
            return (instr & 0xF) == 0 ? 1 : 0;
        }

        private static bool MovBitCmp(int instr)
        {
            var op = instr >> 12;
            return op == 0x4 || op == 0xB || op == 0x9;
        }

        private static int FormatICost(int w)
        {
            var src = SourceMode((w >> 8) & 0xF, (w >> 4) & 3);
            var col = Column(w);
            var cost = FormatI[Row(src), col];
            if(col == 2 && MovBitCmp(w)) cost -= 1;
            return cost;
        }

        // Table 4-9, MSP430 Format II (RRC, SWPB, RRA, SXT, PUSH, CALL).
        private static int FormatIICost(int w)
        {
            var op = (w >> 7) & 7;
            var mode = SourceMode(w & 0xF, (w >> 4) & 3);
            if(op == 4) // PUSH
            {
                return mode == Mode.Memory || mode == Mode.Absolute ? 4 : 3;
            }
            if(op == 5) // CALL
            {
                if(mode == Mode.Absolute) return 6;
                return mode == Mode.Memory ? 5 : 4;
            }
            switch(mode)
            {
                case Mode.Register: return 1;
                case Mode.Indirect:
                case Mode.IndirectIncrement: return 3;
                default: return 4;
            }
        }

        // Table 4-17, CALLA.
        private static int CallaCost(int w)
        {
            switch((w >> 4) & 0xF)
            {
                case 0x5: return (w & 0xF) == 1 ? 6 : 5; // x(Rdst), +1 for SP
                case 0x6:
                case 0x7: return 6; // @Rdst, @Rdst+
                case 0x8:
                case 0x9: return 7; // &abs20, EDE
                default: return 5; // Rdst, #imm20
            }
        }

        // Table 4-19, MOVA/CMPA/ADDA/SUBA, plus the rotates (table 4-17).
        private static int AddressCost(int w)
        {
            var toPc = (w & 0xF) == 0;
            switch((w >> 4) & 0xF)
            {
                case 0x0:
                case 0x1: return toPc ? 5 : 3; // MOVA @Rsrc(+),Rdst; RETA
                case 0x2:
                case 0x3: return toPc ? 6 : 4; // MOVA &abs20 / x(Rsrc),Rdst
                case 0x4:
                case 0x5: return RotateCount(w);
                case 0x6:
                case 0x7: return 4; // MOVA Rsrc,&abs20 / z16(Rdst)
                case 0x8: return toPc ? 3 : 2; // MOVA #imm20,Rdst
                case 0x9:
                case 0xA:
                case 0xB: return 3; // CMPA/ADDA/SUBA #imm20,Rdst
                default: return toPc ? 3 : 1; // register to register
            }
        }

        // Tables 4-17 and 4-18: an instruction after an extension word.
        private int ExtendedCost(int ext, int w)
        {
            var wide = ((ext >> 6) & 1) == 0 && ((w >> 6) & 1) == 1; // .A
            var formatII = (w & 0xFC00) == 0x1000;
            var src = SourceMode(formatII ? (w & 0xF) : ((w >> 8) & 0xF), (w >> 4) & 3);
            var registerOnly = src == Mode.Register && (formatII || ((w >> 7) & 1) == 0);
            var repeat = 1;
            if(registerOnly)
            {
                repeat = (((ext >> 7) & 1) != 0 ? (int)(GetRegister(ext & 0xF).RawValue & 0xF) : (ext & 0xF)) + 1;
            }

            if(formatII)
            {
                var op = (w >> 7) & 7;
                if(op == 4) // PUSHX
                {
                    switch(src)
                    {
                        case Mode.Register:
                        case Mode.Immediate: return wide ? 5 : 4;
                        case Mode.Indirect:
                        case Mode.IndirectIncrement: return wide ? 6 : 4;
                        default: return wide ? 7 : 5;
                    }
                }
                switch(src) // RRCX, RRAX, SWPBX, SXTX
                {
                    case Mode.Register: return 1 + repeat;
                    case Mode.Indirect:
                    case Mode.IndirectIncrement: return wide ? 6 : 4;
                    default: return wide ? 7 : 5;
                }
            }

            var col = Column(w);
            if(registerOnly && col == 0)
            {
                return repeat + 1;
            }
            var cost = (wide ? FormatIXA : FormatIX)[Row(src), col];
            var op1 = w >> 12;
            if(col == 2 && MovBitCmp(w)) cost -= wide ? 2 : 1;
            if(col == 1 && (op1 == 0x4 || op1 == 0x5 || op1 == 0x8)) cost -= 1;
            return cost;
        }

        // DINT, EINT, BIS/BIC/MOV to SR and RETI change GIE themselves.
        private bool WritesStatusRegister(uint pc)
        {
            var w = Word(pc);
            if(w >= 0x1800 && w < 0x2000) w = Word(pc + 2);
            if(w == 0x1300) return true;
            return (w >> 12) >= 4 && (w & 0xF) == 2 && ((w >> 7) & 1) == 0;
        }

        // Rotates: 0000 nnff 010w dddd (n+1 = bit count, f = which, w=1: 16-bit).
        private static bool IsRotate(int w)
        {
            return (w & 0xF0E0) == 0x0040;
        }

        private static int RotateCount(int w)
        {
            return ((w >> 10) & 3) + 1;
        }

        private void Rotate(int w)
        {
            var dst = w & 0xF;
            var func = (w >> 8) & 3;
            var bits = (w & 0x10) != 0 ? 16 : 20;
            var mask = (1UL << bits) - 1;
            var msb = 1UL << (bits - 1);
            var v = GetRegister(dst).RawValue & mask;
            var sr = GetRegister(2).RawValue;
            var c = sr & 1;
            for(var i = 0; i < RotateCount(w); i++)
            {
                if(func == 2) // RLAM
                {
                    c = (v & msb) != 0 ? 1UL : 0UL;
                    v = (v << 1) & mask;
                }
                else // RRCM (carry in), RRAM (sign in), RRUM (zero in)
                {
                    var into = func == 0 ? c : (func == 1 ? ((v & msb) != 0 ? 1UL : 0UL) : 0UL);
                    c = v & 1;
                    v = (v >> 1) | (into * msb);
                }
            }
            sr = (sr & ~0x107UL) | c | (v == 0 ? 2UL : 0UL) | ((v & msb) != 0 ? 4UL : 0UL);
            SetRegister(dst, RegisterValue.Create(v, 32));
            SetRegister(2, RegisterValue.Create(sr, 32));
            PC = RegisterValue.Create(PC.RawValue + 2, 32);
        }

        private const ulong Gie = 0x8;
        private ulong owed;
    }
}
