# Qoder Sites 发布指引（已完成）

> ✅ **已发布**：https://coursenote-qmrksdxnp0m.qoder.zone
> 验证（2026-10-27）：HTTP 200；未登录可访问；3 张界面截图浏览器实测均正常渲染（naturalWidth=1600）
> 站点源码：`qoder/site/index.html`（sha256 与已上传部署一致）
> 站点描述：仓库根 `.coursenote.qoder.site`（含 siteId / deploymentId / uploadId，作为 Qoder Sites 发布的证据）

## 为什么需要这一步
大赛 Qoder 题要求「项目网站必须通过 Qoder Sites 发布」，可见范围设为「任何人可访问」。
Qoder 的 Sites 面板提示：**「在本地工作区让 Agent 生成网站，再确认发布」**。

## 站点内容已经在工作区里（两种路径都放好了）
```
qoder/site/index.html        ← 单页站点（痛点/功能/技术结合实测对照/界面截图/运行说明/仓库链接）
qoder/site/img/*.png         ← 3 张真实界面截图
sites/coursenote/index.html  ← 同一份内容的副本（Qoder 常见约定路径）
sites/coursenote/img/*.png
```

## 操作步骤

### 1）在 Qoder 聊天框里粘贴这句话（让 Agent 建站点）
```
请在本地工作区创建 Qoder 站点（Sites），站点名为 coursenote，
入口文件使用 sites/coursenote/index.html（图片在 sites/coursenote/img/）。
如果 Sites 需要站点放在别的目录，就复制一份过去。
站点内容是 CourseNote 学习资料混合检索助手的介绍页，请勿修改页面内容，
创建完成后告诉我站点路径。
```

### 2）在右侧「站点」面板确认
- 面板里会多出一条站点记录（DATA STATUS / SOURCE: WORKSPACE FILES / PATH / VERIFIED BY: Qoder）
- 点进去核对入口文件与截图是否正常显示

### 3）发布
- 点「发布」/「确认发布」
- **可见范围选择「任何人可访问」**（企业版账号无法设置公开，必须用个人账号）

### 4）取链接
- 复制发布后的完整网址（形如 https://xxx.qoder.site/...）
- **用无痕窗口打开确认未登录也能访问**

### 5）平台提交（600078）
```
Commit id : <分支最新 commit>
Branch    : 2026-competition-ecosystem
作品链接  : 第 4 步的 Qoder Sites 网址
```

## 兜底
若 Qoder Sites 临时不可用，已部署一个可公开访问的同内容站点：
https://wangziye24.github.io/coursenote-site/
（注意：官方要求 Qoder Sites 发布，兜底链接仅应急，正式提交请用上面的 Qoder 网址）
