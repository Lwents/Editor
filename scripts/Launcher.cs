using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class @CLASS@
{
    [STAThread]
    private static int Main()
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(root, "scripts", "@ACTION@.ps1");
            if (!File.Exists(script)) throw new FileNotFoundException("Missing launcher script", script);
            var start = new ProcessStartInfo("powershell.exe",
                "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\"");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardError = true;
            using (var process = Process.Start(start))
            {
                string error = process.StandardError.ReadToEnd();
                process.WaitForExit();
                if (process.ExitCode != 0)
                {
                    string log = Path.Combine(root, ".runtime", "launcher-error.txt");
                    if (String.IsNullOrWhiteSpace(error) && File.Exists(log)) error = File.ReadAllText(log);
                    MessageBox.Show(String.IsNullOrWhiteSpace(error) ? "Cannot start/stop editor. Check .runtime logs." : error,
                        "Editor", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Editor", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
