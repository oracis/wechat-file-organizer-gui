# Windows 代码签名（SignPath Foundation，开源免费）

目标：让 `WeChatFileOrganizer.exe` 带上 **Authenticode 签名**，普通用户双击运行时不再被 Windows SmartScreen 拦截（不再出现「Windows 已保护你的电脑 / 无法验证发布者」）。

证书由 **SignPath Foundation** 持有（私钥保存在 HSM 硬件安全模块里），对符合资格的开源项目**免费**。用户下载到的 exe 会显示发布者为「SignPath Foundation」，并且可验证它确实由本仓库的 CI 构建产生，而非被人篡改。

---

## 前提（本项目已满足）

- 仓库公开（public OSS）—— `oracis/wechat-file-organizer-gui` 是公开仓库 ✅
- 已有至少一个 Release —— 我们已有 v1.0.0 ~ v1.13.0 ✅
- 许可证为 OSI 批准 —— 本项目是 MIT ✅
- 自动化构建 —— CI 工作流 `.github/workflows/build-and-sign.yml` 已就绪 ✅

---

## 一、申请免费签名（一次性，约 1 周审批）

1. 打开 <https://signpath.org/>，点 **Apply / 申请免费代码签名**。
2. 填写项目信息，关联 GitHub 仓库 `oracis/wechat-file-organizer-gui`。
3. 等待审批（通常几天到一两周）。审批通过后会收到邮件。

---

## 二、在 SignPath 控制台配置

1. 安装 **SignPath GitHub App** 并授权它访问本仓库（用于「可信构建」校验——证明 exe 确实由 GitHub Actions 构建）。
2. 在控制台创建以下对象，并记下对应的值：
   - **Organization** → 记下 **Organization ID**
   - **Project** → slug 建议填 `wechat-file-organizer-gui`
   - **Signing Policy** → slug 建议填 `release-signing`，作用域设为 release 分支 / 标签
   - **Artifact Configuration** → 直接用默认的 `default` 即可（根类型为 archive，正好匹配 CI 里 `upload-artifact` 产出的 zip）
3. 生成一个 **API Token**，赋予 **submitter** 权限。

---

## 三、在 GitHub 仓库填入 Secrets / Variables

进入仓库 **Settings → Secrets and variables → Actions**：

**Secrets（保密，不要外泄）**
| Name | Value |
|------|-------|
| `SIGNPATH_API_TOKEN` | 第二步生成的 API Token |

**Variables（可公开）**
| Name | Value |
|------|-------|
| `SIGNPATH_ORGANIZATION_ID` | 你的 Organization ID |
| `SIGNPATH_PROJECT_SLUG` | 项目 slug（如 `wechat-file-organizer-gui`） |
| `SIGNPATH_SIGNING_POLICY_SLUG` | 签名策略 slug（如 `release-signing`） |
| `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | （可选）填 `default` |

> 注意：`SIGNPATH_API_TOKEN` 必须是 **Secret**；其余三个 slug/id 用 **Variable** 即可。

---

## 四、之后怎么发版

填好上面四项后，**直接在本地打 tag 推上去**就行，CI 会自动：构建 → 签名 → 建 Release 并上传签名后的 exe：

```bash
git tag v1.14.0
git push origin v1.14.0
```

也可以到 **Actions** 标签找到 `Build and Sign Windows EXE`，点 **Run workflow** 手动运行并填写版本号。

---

## 五、没填 Secrets 时会怎样

工作流里的「Check SignPath configuration」步骤会检测到缺失，自动**跳过签名、直接发布未签名的 exe**——发布流程绝不会中断。等你把上面四项填好，下一次发版就会自动带上签名，SmartScreen 弹窗随之消失。

---

## 排查

- **Release 创建失败，报权限错误**：到仓库 **Settings → Actions → General → Workflow permissions**，确认设为 **Read and write permissions**。
- **签名步骤报错 / 超时**：检查四个变量名是否完全一致（大小写敏感），以及 SignPath 控制台里 Project / Policy / Artifact Configuration 的 slug 是否匹配。
- **SmartScreen 仍有提示**：新签名证书也需要一定的下载量积累信誉，但相比未签名，提示频率会大幅降低；这是微软机制，非配置问题。
