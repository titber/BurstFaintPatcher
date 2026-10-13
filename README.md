# BurstFaintPatcher — 圣光爆发晕厥判定补丁工具

对单机游戏 **AliceInCradle** 的 `Assembly-CSharp.dll` 做静态字节补丁：把战斗技能
**圣光爆发（Burst）** 释放时的「随机晕厥」判定改为**只在晕厥概率 ≥ 100% 时才晕厥**。

- 适用：`nel.M2PrSkill.runBurst()`
- 原逻辑：`if (X.XORSP() < this.BurstSel.fainted_ratio || num == 0f)` —— 每次释放按概率随机晕厥
- 改为：`if (this.BurstSel.fainted_ratio >= 1f || num == 0f)` —— 仅当概率满 100% 才晕厥
- 由于 `fainted_ratio` 由 `MDAT.calcBurstFaintedRatio` 计算、正常游玩几乎不会到 1.0，
  实际效果等同于「**不再随机晕厥**」

---

## 特点

- **跨版本**：底层引擎用 [dnlib](https://github.com/0xd4d/dnlib) 按 **方法/字段名**
  （`XORSP` / `BurstSel` / `fainted_ratio`）定位，而不是硬编码 IL token，
  因此对 IL 形态相同的不同游戏版本（如 029 / 030）都可用。
- **安全**：补丁前先校验特征码；若 DLL 该段被人为改动或版本不兼容，**拒绝修改、不做任何写入**，
  避免破坏程序集。原地修改会先自动备份 `.bak`。
- **免环境**：打包为单文件 exe，目标机**无需预装 .NET / Python**。

---

## 使用方法

1. 双击 `BurstFaintPatcher.exe`。
2. 点「**浏览...**」选择游戏的 `Assembly-CSharp.dll`
   （位于 `<游戏目录>\AliceInCradle_Data\Managed\Assembly-CSharp.dll`）。
3. 点「**检测状态**」，看状态行：
   - 🟢 **可以打补丁（原始状态）** → 按钮可用，可继续
   - 🔵 **已是目标状态（无需修改）** → 已打过补丁，无需再改
   - 🔴 **疑似被人为改动 / 不兼容** → 已拒绝，不做任何修改
4. （可选）勾选「**原地修改（自动备份 .bak）**」：
   - 勾选 → 直接改写原 DLL，并生成 `Assembly-CSharp.dll.bak` 备份
   - 不勾 → 输出到 `Assembly-CSharp.dll.patched.dll`，不动原文件
5. 点「**打补丁**」，完成后弹窗提示输出位置。

> 建议：游戏更新后重新对新的 DLL 运行本工具；换版本时先备份原 DLL。

---

## 状态码含义（引擎输出）

| status | 含义 | 是否写入 |
| --- | --- | --- |
| `pristine` | 命中原始判定，可安全打补丁 | — |
| `pristine_patched` | 补丁已成功写入 | ✅ |
| `patched` | 已处于目标状态 | ❌（无需改） |
| `modified` | 未找到标准特征码（被改动 / 不兼容） | ❌ 拒绝 |
| `ambiguous` | 特征码不唯一 | ❌ 拒绝 |
| `error` | 出错（如方法体无法解析） | ❌ |

---

## 源码构建

### 目录结构

| 路径 | 说明 |
| --- | --- |
| `gui_patcher.py` | Python + Tkinter 图形界面（选文件 / 检测 / 打补丁 / 调用引擎） |
| `engine/Program.cs` | C# + dnlib 补丁引擎：按**名**定位 `runBurst`，输出状态 JSON |
| `engine/BurstFaintEngine.csproj` | 引擎工程（dnlib 通过 NuGet 引用，无需本机路径） |
| `LICENSE` | MIT 许可证 |

### 1. 编译补丁引擎（需 .NET 8 SDK）

```bash
cd engine
dotnet publish -c Release -r win-x64 --self-contained true \
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true \
  -p:DebugType=none -o publish
```

产物：`engine/publish/BurstFaintEngine.exe`（自包含单文件，内含 .NET 运行时）。

### 2. 打包 GUI（需 Python 3.10+ 与 PyInstaller）

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name BurstFaintPatcher \
  --distpath publish_py --workpath build_py \
  --add-binary "engine/publish/BurstFaintEngine.exe;." \
  --add-data "LICENSE;." \
  gui_patcher.py
```

> `--add-binary` / `--add-data` 的分隔符在 **Windows 上是 `;`**（Linux/macOS 上是 `:`）。

产物：`publish_py/BurstFaintPatcher.exe`（单文件，已内嵌引擎与 LICENSE）。

### 3. 直接以源码运行（不打包）

```bash
python gui_patcher.py
```

> 需先保证 `engine/publish/BurstFaintEngine.exe` 已存在，否则界面调用引擎时会报「未找到补丁引擎」。

---

## 常见问题

- **点「检测状态」会顿一下？**
  首次运行需解压内置 .NET 运行时。程序启动时已在后台预热，首次点击后后续会明显变快。
- **提示「疑似被人为改动 / 不兼容」？**
  说明该 DLL 的目标段已不是你预期的原始 IL，工具出于安全拒绝修改；请确认 DLL 版本。
- **改了还能玩吗？**
  仅改这一处判定，不影响程序集其它功能；备份文件可随时还原。

---

## 许可证

本项目以 **MIT License** 发布，版权归 **titber** 所有（`Copyright (c) 2026 titber`）。
详见 [LICENSE](LICENSE)。程序内「关于/许可」按钮亦可查看。
