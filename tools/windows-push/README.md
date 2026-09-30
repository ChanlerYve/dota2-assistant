# 推送工具（仅本机环境需要）

这个目录**不属于 dota2-assistant 项目**，它解决的是本机网络与沙箱的限制。

## 为什么需要它

排查结论（实测）：

| 目标 | 结果 |
|---|---|
| `github.com:443` | ❌ TCP 超时（21s），`git push` 走 HTTPS 必然失败 |
| `github.com:80` | ✅ 通，但 GitHub 只用来跳转 HTTPS |
| `api.github.com:443` | ✅ 通（建仓库、查 key、核对内容都靠它） |
| `ssh.github.com:443` | ✅ 通（GitHub 官方的 SSH-over-443 通道） |
| `github.com:22` | ❌ 被屏蔽 |

所以必须走 **SSH + 443 端口**。但 OpenSSH 在这台机器上还有两个障碍：

1. **私钥权限检查**：OpenSSH 拒绝使用权限过宽的私钥，而 DSH 沙箱不允许收紧 ACL；
2. **ssh-agent 起不来**：`ssh-agent` 服务未启用，且沙箱禁止创建命名管道。

因此改用 **paramiko（纯 Python）** 直接实现 git 的 remote helper 协议——
不 fork `ssh.exe`、不检查文件权限、不用命名管道。

## 组成

| 文件 | 作用 |
|---|---|
| `push_now.py` | **推荐入口**：要 token 时会明确提示并让你直接输入（无回显），全程自动注册/撤销临时密钥 |
| `git-remote-d2a.exe` | git 要求的可执行入口（git 在 Windows 上不会自动补 `.cmd` 后缀） |
| `git-remote-d2a.cs` | 上面那个 exe 的源码，用 `csc /target:exe` 编译 |
| `git_remote_paramiko.py` | 真正的协议实现：paramiko 走 SSH，处理 `capabilities`/`list`/`push` |
| `push.cmd` | 备用入口：需要你先自己准备好 `push_key` 私钥 |
| `selftest_prompt.py` | 自检：验证「读 token」在控制台/管道下都不会挂死 |

## 推送（一条命令，token 按需提示）

```powershell
python d2a-push-tool\push_now.py main
```

**需要 token 时它会主动告诉你**，并且：

1. 打印一个醒目的提示框（仓库地址、需要的权限、三种给 token 的方式）；
2. 自动在浏览器打开 https://github.com/settings/tokens ；
3. 让你**直接在终端里粘贴**——不回显、不进 shell 历史、120 秒超时不会挂死。

需要的权限（fine-grained token，只授权 `ChanlerYve/dota2-assistant`）：

| 权限 | 级别 | 用途 |
|---|---|---|
| Contents | Read and write | 推送代码 |
| Git SSH keys | Read and write | 注册/撤销临时密钥 |

也可以绕过提示直接提供 token：

```powershell
$env:D2A_TOKEN = "ghp_xxx"; python d2a-push-tool\push_now.py main
```

> token 会在入口自动清洗（去 BOM、零宽字符、空白、非 ASCII）——
> 这不是洁癖：PowerShell 管道确实会给输入加 UTF-8 BOM，那个字符进 HTTP 头会直接崩。

## 备用方案（自己管理密钥）

```powershell
# 准备好本目录下的 push_key / push_key.pub，并把公钥加到你自己的 GitHub 账号
.\push.cmd
```

## 已知限制

- **只能推送，不能拉取**：`fetch` 未实现（本机只需要 push）。
  需要拉取时请用浏览器下载 zip，或配一个能访问 `github.com:443` 的代理。
- `git` 的 remote 用的是自定义 scheme：`d2a::git@ssh.github.com:OWNER/REPO.git`。
  这是 remote helper 的机制，不是笔误。
- 依赖 `paramiko`（`pip install paramiko`）。

## 更省事的替代方案

如果你有能访问 GitHub 的代理/VPN，直接用它就好，本目录可以整个删掉：

```powershell
git remote set-url origin https://github.com/ChanlerYve/dota2-assistant.git
$env:HTTPS_PROXY = "http://127.0.0.1:7890"   # 换成你的代理端口
git push
```
