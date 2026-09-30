// git-remote-d2a 的 Windows 原生入口。
//
// 为什么需要它：git 在 Windows 上会去 exec-path 里查找名为 `git-remote-<scheme>`
// 的**可执行文件**，不会自动补 .cmd/.bat 后缀，因此批处理入口无法被 git 调用。
// 这里编译出一个真正的 .exe，只做一件事：把参数原样转交给同目录的 Python 实现。
//
// 用 csc.exe 编译（.NET Framework 自带，无需额外依赖）：
//   csc /nologo /target:exe /out:git-remote-d2a.exe git-remote-d2a.cs

using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class Program
{
    private static int Main(string[] args)
    {
        string exeDir = Path.GetDirectoryName(Process.GetCurrentProcess().MainModule.FileName);
        string script = Path.Combine(exeDir, "git_remote_paramiko.py");

        if (!File.Exists(script))
        {
            Console.Error.WriteLine("git-remote-d2a: 找不到 " + script);
            return 1;
        }

        var psi = new ProcessStartInfo
        {
            FileName = "python",
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = false,
        };
        psi.Arguments = Quote(script) + " " + JoinQuoted(args);

        // 让 paramiko 等依赖可见（脚本自身不 import 项目内模块，这里主要防意外）
        string path = Environment.GetEnvironmentVariable("PYTHONPATH");
        psi.EnvironmentVariables["PYTHONPATH"] = string.IsNullOrEmpty(path)
            ? exeDir
            : exeDir + Path.PathSeparator + path;
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

        Process p;
        try
        {
            p = Process.Start(psi);
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("git-remote-d2a: 无法启动 python: " + e.Message);
            return 1;
        }

        // 把 git 写给我们 stdin 的字节原样转发给 python（二进制安全）
        var stdin = Console.OpenStandardInput();
        var childIn = p.StandardInput.BaseStream;
        var pump = new Action(delegate
        {
            try
            {
                byte[] buf = new byte[65536];
                int n;
                while ((n = stdin.Read(buf, 0, buf.Length)) > 0)
                {
                    childIn.Write(buf, 0, n);
                    childIn.Flush();
                }
            }
            catch (IOException) { }
            finally
            {
                try { childIn.Flush(); childIn.Close(); } catch (IOException) { }
            }
        });
        pump.BeginInvoke(null, null);

        // python 的 stdout 原样转给 git
        var stdout = Console.OpenStandardOutput();
        var childOut = p.StandardOutput.BaseStream;
        byte[] obuf = new byte[65536];
        int m;
        while ((m = childOut.Read(obuf, 0, obuf.Length)) > 0)
        {
            stdout.Write(obuf, 0, m);
            stdout.Flush();
        }

        p.WaitForExit();
        return p.ExitCode;
    }

    private static string Quote(string s)
    {
        return "\"" + s.Replace("\"", "\\\"") + "\"";
    }

    private static string JoinQuoted(string[] args)
    {
        var sb = new StringBuilder();
        foreach (string a in args)
        {
            if (sb.Length > 0) sb.Append(' ');
            sb.Append(Quote(a));
        }
        return sb.ToString();
    }
}
