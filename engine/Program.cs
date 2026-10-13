using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using dnlib.DotNet;
using dnlib.DotNet.Emit;

// Cross-version Burst faint-check patcher engine.
// Uses dnlib to locate the faint check by NAME (XORSP / BurstSel / fainted_ratio)
// so it works on any game version whose IL shape is the same, regardless of token values.
//
// Usage:
//   BurstFaintEngine scan  <Assembly-CSharp.dll>
//   BurstFaintEngine patch <Assembly-CSharp.dll> [--inplace] [--out:<file>]
//
// Output JSON to stdout:
//   {"status":"pristine"|"patched"|"modified"|"ambiguous"|"error","detail":"...","out":""}

class Engine
{
    static bool IsBlt(OpCode oc)
    {
        return oc == OpCodes.Blt || oc == OpCodes.Blt_S
            || oc == OpCodes.Blt_Un || oc == OpCodes.Blt_Un_S;
    }
    static bool IsBge(OpCode oc)
    {
        return oc == OpCodes.Bge || oc == OpCodes.Bge_S
            || oc == OpCodes.Bge_Un || oc == OpCodes.Bge_Un_S;
    }

    // Returns the index of the `call XORSP` instruction if the faint-check pattern
    // (call XORSP; ldarg.0; ldfld BurstSel; ldfld fainted_ratio; <blt>) starts at that index.
    static int FindPristine(IList<Instruction> ins)
    {
        for (int i = 0; i + 4 < ins.Count; i++)
        {
            if (ins[i].OpCode != OpCodes.Call) continue;
            var mr = ins[i].Operand as IMethod;
            if (mr == null || mr.Name != "XORSP") continue;
            if (ins[i + 1].OpCode != OpCodes.Ldarg_0) continue;
            if (ins[i + 2].OpCode != OpCodes.Ldfld) continue;
            if ((ins[i + 2].Operand as IField)?.Name != "BurstSel") continue;
            if (ins[i + 3].OpCode != OpCodes.Ldfld) continue;
            if ((ins[i + 3].Operand as IField)?.Name != "fainted_ratio") continue;
            if (!IsBlt(ins[i + 4].OpCode)) continue;
            return i;
        }
        return -1;
    }

    // Returns index of `ldfld fainted_ratio` if it's followed by `ldc.r4 1.0` + a `bge`
    // (the already-patched form), else -1.
    static int FindPatched(IList<Instruction> ins)
    {
        for (int i = 0; i + 2 < ins.Count; i++)
        {
            if (ins[i].OpCode != OpCodes.Ldfld) continue;
            if ((ins[i].Operand as IField)?.Name != "fainted_ratio") continue;
            if (ins[i + 1].OpCode != OpCodes.Ldc_R4) continue;
            if (!(ins[i + 1].Operand is float f) || Math.Abs(f - 1.0f) > 1e-6f) continue;
            if (!IsBge(ins[i + 2].OpCode)) continue;
            return i;
        }
        return -1;
    }

    // Emit a JSON string value safely as pure ASCII (\" \\ and non-ASCII -> \uXXXX),
    // so the caller can decode as UTF-8/ASCII without any codepage ambiguity.
    static string JStr(string s)
    {
        if (s == null) return "";
        var b = new StringBuilder();
        foreach (char c in s)
        {
            if (c == '"') b.Append("\\\"");
            else if (c == '\\') b.Append("\\\\");
            else if (c == '\n') b.Append(" ");
            else if (c == '\r') b.Append(" ");
            else if (c < 0x20) b.Append("\\u").Append(((int)c).ToString("x4"));
            else if (c < 0x80) b.Append(c);
            else b.Append("\\u").Append(((int)c).ToString("x4"));
            if (b.Length > 8000) break; // safety
        }
        return b.ToString();
    }

    static void EmitJson(string status, string detail, string outPath)
    {
        var sb = new StringBuilder();
        sb.Append('{');
        sb.Append("\"status\":\"").Append(status).Append('"');
        sb.Append(",\"detail\":\"").Append(JStr(detail)).Append('"');
        sb.Append(",\"out\":\"").Append(JStr(outPath)).Append('"');
        sb.Append('}');
        Console.Out.WriteLine(sb.ToString());
    }

    static int Main(string[] args)
    {
        try
        {
            if (args.Length >= 1 && args[0] == "version")
            {
                // 轻量子命令：不加载 DLL，仅确认引擎可用并触发运行时解压（预热）。
                EmitJson("ok", "BurstFaintEngine ready", "");
                return 0;
            }
            if (args.Length < 2)
            {
                EmitJson("error", "usage: scan|patch <dll> [--inplace] [--out:<f>]", "");
                return 1;
            }
            string cmd = args[0];
            string dll = args[1];
            bool inplace = false; string outPath = null;
            for (int k = 2; k < args.Length; k++)
            {
                if (args[k] == "--inplace") inplace = true;
                else if (args[k].StartsWith("--out:")) outPath = args[k].Substring(6);
            }
            if (!File.Exists(dll))
            {
                EmitJson("error", "文件不存在: " + dll, "");
                return 1;
            }

            var mod = ModuleDefMD.Load(dll);
            TypeDef t = null;
            foreach (var td in mod.GetTypes())
            {
                if (td.FullName == "nel.M2PrSkill") { t = td; break; }
            }
            if (t == null)
            {
                EmitJson("error", "未找到类型 nel.M2PrSkill", "");
                mod.Dispose();
                return 1;
            }
            var m = t.FindMethod("runBurst");
            if (m == null || m.Body == null)
            {
                EmitJson("error", "未找到方法 nel.M2PrSkill.runBurst 或无可解析的方法体", "");
                mod.Dispose();
                return 1;
            }
            var ins = m.Body.Instructions;

            int pristine = FindPristine(ins);
            int patched = FindPatched(ins);

            if (cmd == "scan")
            {
                if (pristine >= 0)
                {
                    EmitJson("pristine", "命中原始晕厥判定(call XORSP ... blt)。可以打补丁。", "");
                }
                else if (patched >= 0)
                {
                    EmitJson("patched", "已为目标状态(fainted_ratio >= 1.0 分支)。无需修改。", "");
                }
                else
                {
                    EmitJson("modified", "未找到标准特征码：该 DLL 的 runBurst 晕厥判定疑似被人为改动或版本不兼容，拒绝修改以避免破坏程序集。", "");
                }
                mod.Dispose();
                return 0;
            }

            // patch
            if (cmd == "patch")
            {
                if (pristine < 0)
                {
                    if (patched >= 0)
                        EmitJson("patched", "已经是目标状态，未做任何修改。", "");
                    else
                        EmitJson("modified", "未找到标准特征码，疑似被人为改动或版本不兼容，已中止，未写入任何字节。", "");
                    mod.Dispose();
                    return 1;
                }

                // Replace:
                //   call XORSP            (index pristine)
                //   ldarg.0
                //   ldfld BurstSel
                //   ldfld fainted_ratio
                //   blt.s TARGET
                // with:
                //   ldarg.0
                //   ldfld BurstSel
                //   ldfld fainted_ratio
                //   ldc.r4 1.0
                //   bge.s TARGET   (same TARGET)
                var cmp = ins[pristine + 4];
                var target = cmp.Operand;   // Instruction or Instruction[]

                ins.RemoveAt(pristine);                 // drop the call XORSP
                // now the blt.s sits at pristine+3
                int cidx = pristine + 3;
                ins.Insert(cidx, Instruction.Create(OpCodes.Ldc_R4, 1.0f));
                // replace the blt.s with bge (long form; dnlib keeps it valid)
                Instruction bgeInstr;
                if (target is Instruction ti)
                    bgeInstr = Instruction.Create(OpCodes.Bge, ti);
                else
                    bgeInstr = Instruction.Create(OpCodes.Bge, (Instruction[])target);
                ins[cidx + 1] = bgeInstr;

                string finalOut = outPath;
                if (inplace)
                {
                    string bak = dll + ".bak";
                    if (!File.Exists(bak)) File.Copy(dll, bak, false);
                    finalOut = dll;
                }
                else if (finalOut == null)
                {
                    finalOut = dll + ".patched.dll";
                }
                mod.Write(finalOut);
                mod.Dispose();
                EmitJson("pristine_patched", "已打补丁：晕厥判定改为 fainted_ratio >= 1.0。", finalOut);
                return 0;
            }

            EmitJson("error", "unknown command: " + cmd, "");
            return 1;
        }
        catch (Exception ex)
        {
            EmitJson("error", "异常: " + ex.GetType().Name + ": " + ex.Message, "");
            return 1;
        }
    }
}
