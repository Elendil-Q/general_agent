# Chrome 100 CSS 兼容性改进设计

## 背景

DeerFlow 前端（Next.js 16 + Tailwind CSS v4）在 Chrome 100 上出现严重布局崩溃：
界面仅显示左上约 1/4，对话框挤成一条线无法输入。根因是 CSS 使用了一批
Chrome 100 不支持的特性，且这些特性不会被 PostCSS/SWC 自动降级。

## 目标基线

Chrome 100+（2022 年 3 月发布）。

## 不兼容特性清单

| 特性 | 用量 | Chrome 最低支持 | 差距 | 严重度 |
| --- | --- | --- | --- | --- |
| `oklch()` 颜色函数 | 61 处（globals.css） | Chrome 111 | 11 版本 | 致命 |
| `color-mix()` | 1 处（globals.css） | Chrome 111 | 11 版本 | 低 |
| `svh` viewport 单位 | 2 处（sidebar.tsx） | Chrome 108 | 8 版本 | 高 |
| Range 语法媒体查询 | 4 处（globals.css） | Chrome 104 | 4 版本 | 中 |
| `:has()` 选择器 | 多处（Tailwind variant） | Chrome 105 | 5 版本 | 低（降级） |

## 方案：纯源码重写（方案 B）

不引入新构建依赖，通过脚本计算和手动修改完成全部兼容性修复。

## 1. oklch -> rgb 颜色转换

**文件**：`src/styles/globals.css`

**方法**：用 Python 脚本实现 oklch(L, C, H) -> sRGB 的精确数学变换：

1. oklch -> oklab: a = C * cos(H deg), b = C * sin(H deg)
2. oklab -> linear sRGB (matrix transform + cube root)
3. linear sRGB -> sRGB (gamma encoding)
4. clamp to [0, 255] and round

**处理范围**：
- `:root` 块：31 个 CSS 变量
- `.dark` 块：30 个 CSS 变量
- 含 alpha 的值（如 `oklch(1 0 0 / 10%)`）转换为 `rgba(r, g, b, alpha)`

**替换策略**：保持变量名和 CSS 结构不变，仅替换值。例如：
```css
/* Before */
--background: oklch(0.9855 0.0098 87.47);
/* After */
--background: rgb(252, 250, 244);
```

**精度说明**：所有主题色均在 sRGB 色域内，oklch -> sRGB 转换的视觉差异可忽略。

## 2. color-mix -> 静态颜色

**文件**：`src/styles/globals.css`（KaTeX 滚动条样式）

**方法**：`color-mix(in oklch, var(--muted-foreground) 35%, transparent)`
等价于 `muted-foreground` 颜色以 35% 不透明度叠加。在完成 oklch -> rgb 转换后，
直接用 muted-foreground 的 rgb 值生成 `rgba(r, g, b, 0.35)`。

## 3. svh -> vh 渐进增强

**文件**：`src/components/ui/sidebar.tsx`

**方法**：在 `svh` 类前加 `vh` 回退类，利用 CSS 层叠规则：

```tsx
// Before
"group/sidebar-wrapper ... flex min-h-svh w-full"
"fixed inset-y-0 z-10 hidden h-svh w-(--sidebar-width) ..."

// After
"group/sidebar-wrapper ... flex min-h-screen min-h-svh w-full"
"fixed inset-y-0 z-10 hidden h-screen h-svh w-(--sidebar-width) ..."
```

Chrome 100 不识别 `min-h-svh`/`h-svh`，回退到 `min-h-screen`/`h-screen`（100vh）。
现代浏览器两者都识别，`svh` 覆盖 `vh`，保持原行为。

## 4. Range media query -> min-width

**文件**：`src/styles/globals.css`（`.container-md`）

**方法**：将 CSS Media Queries Level 4 的 range 语法改为 Level 3 语法：

```css
/* Before */
@media (width >= 40rem) { max-width: 40rem; }

/* After */
@media (min-width: 40rem) { max-width: 40rem; }
```

4 个断点（40rem / 48rem / 64rem / 80rem）全部修改。

## 5. :has() 降级（不改代码）

`:has()` 选择器通过 Tailwind 的 `has-data-[...]`、`has-[...]`、`group-has-[...]`
variant 编译生成。Chrome 100 不支持 `:has()`，相关规则被丢弃。

**受影响的样式**（均为次要视觉/布局增强）：
- 侧栏 inset 变体背景色（`has-data-[variant=inset]:bg-sidebar`）
- 卡片头部双列网格（`has-data-[slot=card-action]:grid-cols-[1fr_auto]`）
- 输入组图标布局（`has-[>svg]:grid-cols-[...]`）
- 各种 `group-has-[...]` 条件样式

这些在 Chrome 100 上回退到默认样式，不影响核心交互功能（聊天、输入、发送）。

## 6. 验证

1. `pnpm build` 确保无编译错误
2. 检查构建输出 CSS 不含 `oklch(`、`color-mix(`、`svh`、`width >=` 语法
3. 在 Chrome 100 或等价环境验证布局恢复（如条件允许）

## 不在范围内

- 不添加 PostCSS 插件或构建时自动转换（避免新依赖和插件兼容性风险）
- 不重写 `:has()` 相关组件（降级可接受）
- 不处理 IE 或 Chrome 80- 的兼容性
- 不修改 Tailwind v4 配置或 shadcn 组件生成流程

## 风险

- 未来通过 shadcn registry 重新生成组件可能重新引入 oklch 颜色。需在
  `AGENTS.md` 中记录此约束。
- oklch -> sRGB 转换可能对广色域显示器有轻微色差，但所有主题色在 sRGB 内。
