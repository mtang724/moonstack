# MoonStack — 月食一键堆栈后期

把一晚上的月食 RAW 自动变成每个阶段一张成片 + 全过程串珠合成图。
流程：RAW 解码 → 找月亮 → 质量打分剔除 → 阶段自动分组 → 亚像素对齐 + HDR 感知堆栈 →
PixInsight BXT/NXT → 拉伸/色调映射 → Photoshop 分层 PSD → HTML 报告。

## 运行

```powershell
cd C:\Users\Ming\Documents\MoonStack
C:\Users\Ming\anaconda3\python.exe run.py                # 全流程（约 6 分钟，96 张 RAF）
C:\Users\Ming\anaconda3\python.exe run.py --from stack   # 从某个阶段继续
C:\Users\Ming\anaconda3\python.exe run.py --stage finish # 只重跑一个阶段（调参时）
C:\Users\Ming\anaconda3\python.exe run.py --no-pi --no-ps # 不用 PixInsight / Photoshop
```

阶段顺序：`analyze → group → stack → pixinsight → finish → photoshop → report`。
每个阶段的结果都落在 `output/*.json`，所以任何阶段都能单独重跑。

**一定要用 anaconda 的 python**（3.11，装了 rawpy/cv2/skimage/tifffile/exifread）；
PATH 里的 `python` 是 3.14，没有这些包。

## 目录

```
Eclipse/            原片 RAF + JPG（JPG 只用来读 EXIF）
moonstack/          流水线代码
  config.py         所有可调参数（DEFAULTS），项目根目录放 config.json 可覆盖
  exif.py           曝光/ISO/光圈/焦距/时间
  raw.py            rawpy 解码 → 线性 sRGB 16bit（日光白平衡）+ 溢出像素掩膜
  detect.py         固定半径 Hough 找月盘中心（残月也能找到真实圆心）
  analyze.py        阶段1：解码、裁 1024² 小图缓存、清晰度等特征
  phases.py         阶段2：按日照面积/曝光档位/时间窗分组，包围曝光单独成组
  align.py          相位相关 + ECC（平移+微小旋转）亚像素对齐
  stack.py          阶段3：链式对齐、光度归一、噪声归一 sigma-clip 加权堆栈
  pi_bridge.py      阶段4：生成 PJSR 脚本，headless 跑 PixInsight BXT+NXT
  finish.py         阶段5：白平衡、拉伸/局部色调映射、串珠合成
  ps_bridge.py      阶段6：生成 JSX，Photoshop 出分层 PSD + 合成 PSD
  report.py         阶段7：自包含 HTML 报告
output/
  frames.json       每张原片的所有测量值
  groups.json       分组、选片、对齐位移、输出路径
  cache/            每帧裁剪缓存 (.npz) + 缩略图
  stacks/           线性堆栈 (float32 TIFF)
  pi_in/ pi_out/    PixInsight 输入/输出
  final/            成片 16bit TIFF + JPG，00_composite.jpg 串珠图
  photoshop/        分层 PSD + JPG
  report.html       报告
  moonstack.log     全部日志
```

## 关键设计决定（为什么这样做）

- **辐射度归一化**：每帧换算成 `counts × N² / (t × ISO)`，不同曝光才能一起堆。
  光圈项不能省：f/6.3 与 f/13 差 4.3 倍，漏掉它分组会全错。
- **HDR 感知堆栈**：溢出像素按掩膜剔除，权重 ∝ 光子信噪比（长曝光权重大），
  所以包围曝光组和混合曝光的深偏食组不需要单独的 HDR 合成步骤。
- **链式对齐**：每帧对齐到"已对齐帧中曝光最接近的那张"。1/1000 s 的细亮边没法直接对
  上 1/2 s 的红月盘，但包围曝光阶梯上相邻两级总能对上。相位相关只在与月盘圆心先验
  相差 <4 px 时采信（细弯月会给出伪峰），然后 ECC 精调。
- **噪声归一的 sigma-clip**：残差乘 √权重再比较，否则短曝光帧会因为自身噪声大被整帧剔掉。
- **清晰度只在相近曝光之间比较**：1/8 s 永远比 0.4 s 锐，但 0.4 s 才有本影信号。
  另外按"未跟踪拖影 = 14.5″/s × t ÷ 像素尺度"硬剔除拖影 >6 px 的帧（1.5 s 那张）。
- **白平衡**：以日照月面为中性灰（在最亮那组测），同一组乘数用于所有阶段，本影的红
  是相对于它的真实颜色。
- **深偏食/HDR 的显示**：对数亮度分成平滑底层（双边滤波）+ 细节，底层压到 4.5 档，
  细节 ×1.2；只对月盘做，天空保持线性拉伸不抬噪。全局 asinh 拉伸试过，本影发灰发粉，弃用。
- **细亮边帧的定位**：1/1000 s 那种只剩一条弧的帧，圆心沿弧线方向不可定；先用固定机位下月亮
  匀速漂移模型（对整盘可见的帧做线性拟合）预测圆心，再用径向射线测亮边半径相对参考帧的偏差，
  只沿弧法线方向修正。ECC 对光滑细弧会滑动，不能单独依赖。
- **圆心测量**：粗定位用固定半径 Hough；精定位用 720 条径向射线找亮度陡降点做圆拟合，置信度 =
  有效射线比例（整盘 ≈1，半月 ≈0.6，细弧 <0.4）。Canny + Hough 试过，天空噪声会投出伪圆心。
- **饱和区处理**：长曝光里全部溢出的亮边没有颜色信息，按"任一通道接近平台值"的软权重渐变为中性，
  避免二值掩膜留下的锯齿边和青紫色圈；天空区去色+轻微平滑，去掉亮边外侧的彩色噪点。
- **单帧组**：没有 sigma-clip，用 5×5 中值剔热噪点（X-Trans 解拜耳会把一个热点糊成 2–3 px）。
- **整场对比的锐度**：某帧比全场同曝光最好的差 55% 以上视为手抖直接剔除（21:07 那张 0.625 s）。
- **分组标签**：这次没有拍到全食，最深约 96%，所以最深阶段叫 `deep`（食甚附近）而不是 total。

## 外部软件调用方式（都验证过）

- PixInsight 1.9.3：`PixInsight.exe --automation-mode -n --no-splash -r=<script.js> --force-exit`
  后台启动，轮询脚本写的 done 文件；PJSR 里 `new BlurXTerminator` / `new NoiseXTerminator`
  参数名见 pi_bridge.py。已有实例在跑时再传 `-r` 只会转发给它。
- Photoshop 2025 (26.10)：`Photoshop.exe script.jsx`。**坑**：订阅未激活时第一个文件能开、
  之后 `app.open` 一律报 "open options are incorrect"——不是脚本问题，激活后就好了。
  `DocumentFill` 没有 BLACK，要先设 `app.backgroundColor` 再用 BACKGROUNDCOLOR。
  PSD 是 16-bit，PIL 读不了，用 Photoshop 或 psd-tools 看。
- GraXpert 也有 CLI（`GraXpert.exe -cli -cmd denoising`），目前没用。

## 数据现状（2026-08-27 拍摄，X-T30 + 18-300 @300mm，固定三脚架）

- 96 帧：19:19 满月 1 帧；19:38–20:09 偏食 1/500–1/180 s；21:02–21:41 深偏食 0.125–1.5 s；
  21:22 一组 1/2→1/1000 s 包围曝光。
- 月盘半径 354 px（EXIF 先验 362，用满月帧拟合修正）。
- 分成 16 组；剔除 ~15 帧（拖影、软、溢出过多）。

- **深偏食缺角的"抢救"（rescue.py）**：除 21:22 包围曝光外，其他深偏食组的日照缺角在所有帧里都溢出
  （1/8 s 已溢出 4–5%，要 ≤1/30 s 才不溢出）。做法：把 19:19 满月堆栈（同一块月面）旋转对齐到该组
  （搜索场旋转角 + ECC，相关系数 0.73–0.82），缺角内亮度 = 边界亮度平滑延续 × 满月纹理（反照率/局部均值）
  × 亮度剖面，颜色从边界色渐变到日照中性色。亮度剖面取自 21:22 包围曝光组**实测**的缺角径向剖面
  （亮边往里 50 px 暗 13 倍、100 px 暗 60 倍），按每组缺角的宽度拉伸、按每条射线的缺角深度缩放，
  上限为满日照亮度。之前的"平台 × 缓升 3 倍"版本看起来像一块平的白斑，用户一眼就看出来了。
  按物理外推半影梯度（平面拟合、逐射线二次拟合）也试过，要么低于溢出电平要么出辐条伪影和蓝晕。
  `rescue_highlights` 可关。
- **单帧组亮边旁的黑点**：X-Trans 解拜耳在饱和像素旁边会吐出接近 0 的洞，多帧组被 sigma-clip 盖掉，
  单帧组由 rescue 一并重建。

## 分享页

`python -m moonstack.share` 生成 `output/share.html`（自包含，可发布成 artifact）。

## 待改进

- 横向色差：已对 R/B 通道做仿射对齐（ECC），但深偏食组 B 通道信号太弱拟合常失败会跳过。
- 深偏食组里全帧溢出的亮边只能是白的，拍摄时应配一张短曝光（21:22 那组包围曝光就是范例）。
- 亮边下方的青色带在包围曝光组里是真实的（臭氧层蓝边，B/G≈1.3–1.5），不是伪影。

## 手机竖版

`finish` 阶段末尾自动出三张 1080×2340：`phone_grid.jpg`（每行 2 个、共 6 个，用户选定的版式）、
`phone_arc.jpg`（弧线排列）、`phone_hero.jpg`（最深阶段大图 + 序列小图）。挑选规则：满月、最深（优先包围曝光 HDR）、最后一张必选，
其余按本影覆盖率差异最大化；`config.json` 里 `phone.moons` / `width` / `height` / `caption` 可改。
单独重跑：`python -m moonstack.phone`。
