using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("ZeroZeta — LiningOps Copilot")]
[assembly: AssemblyDescription("ZeroZeta RefractoryAI — LiningOps Copilot Desktop Application")]
[assembly: AssemblyCompany("ZeroZeta")]
[assembly: AssemblyProduct("ZeroZeta LiningOps Copilot")]
[assembly: AssemblyVersion("2.1.0.0")]
[assembly: AssemblyFileVersion("2.1.0.0")]

namespace LiningOpsDesktop
{
    static class Program
    {
        private const string DefaultServerUrl = "http://65.1.125.127";

        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string exeName = Path.GetFileNameWithoutExtension(Application.ExecutablePath) ?? "";
            bool isSetupInstaller = exeName.IndexOf("Setup", StringComparison.OrdinalIgnoreCase) >= 0
                                 || exeName.IndexOf("Install", StringComparison.OrdinalIgnoreCase) >= 0;

            string serverUrl = Environment.GetEnvironmentVariable("REFRACTORY_SERVER_URL");
            if (string.IsNullOrWhiteSpace(serverUrl))
            {
                serverUrl = DefaultServerUrl;
            }

            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--install" || args[i] == "/install")
                {
                    isSetupInstaller = true;
                }
                else if ((args[i] == "--server" || args[i] == "-s") && i + 1 < args.Length)
                {
                    serverUrl = args[i + 1];
                }
            }
            serverUrl = serverUrl.TrimEnd('/');

            try
            {
                string installDir = GetInstallDirectory();
                string uiDir = EnsureEmbeddedAssetsExtracted(installDir);
                string installedExe = EnsureBinaryInstalled(installDir);
                string iconPath = Path.Combine(installDir, "app_icon.ico");

                if (isSetupInstaller)
                {
                    CreateShortcuts(installedExe, iconPath, forceCreate: true);
                    DialogResult res = MessageBox.Show(
                        "ZeroZeta — LiningOps Copilot has been installed successfully!\n\n" +
                        "• Installed to: " + installDir + "\n" +
                        "• Desktop shortcut created: \"ZeroZeta LiningOps Copilot\"\n" +
                        "• Connected Server: " + serverUrl + "\n\n" +
                        "Would you like to launch ZeroZeta — LiningOps Copilot now?",
                        "ZeroZeta — LiningOps Copilot Setup",
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Information
                    );
                    if (res != DialogResult.Yes)
                    {
                        return;
                    }
                }
                else
                {
                    // Automatically ensure Desktop shortcut exists on first run
                    CreateShortcuts(installedExe, iconPath, forceCreate: false);
                }

                string appHtmlPath = Path.Combine(uiDir, "app.html");
                if (!File.Exists(appHtmlPath))
                {
                    MessageBox.Show(
                        "Could not locate or extract ui/app.html.",
                        "ZeroZeta — LiningOps Copilot Error",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }

                string fileUri = new Uri(appHtmlPath).AbsoluteUri;
                string targetUrl = fileUri + "?api=" + Uri.EscapeDataString(serverUrl);

                if (!LaunchAppWindow(targetUrl))
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = targetUrl,
                        UseShellExecute = true
                    });
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Failed to launch ZeroZeta — LiningOps Copilot:\n\n" + ex.Message,
                    "ZeroZeta — LiningOps Copilot",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }

        private static string GetInstallDirectory()
        {
            string baseAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string installDir = Path.Combine(baseAppData, "LiningOpsCopilot");
            Directory.CreateDirectory(installDir);
            return installDir;
        }

        private static string EnsureBinaryInstalled(string installDir)
        {
            string currentExe = Application.ExecutablePath;
            string targetExe = Path.Combine(installDir, "LiningOps_Copilot_Desktop.exe");
            try
            {
                if (!string.Equals(Path.GetFullPath(currentExe), Path.GetFullPath(targetExe), StringComparison.OrdinalIgnoreCase))
                {
                    File.Copy(currentExe, targetExe, true);
                }
            }
            catch
            {
                if (!File.Exists(targetExe))
                    return currentExe;
            }
            return targetExe;
        }

        private static string EnsureEmbeddedAssetsExtracted(string installDir)
        {
            string extractRoot = Path.Combine(installDir, "ui_v2");
            Directory.CreateDirectory(extractRoot);

            Assembly asm = Assembly.GetExecutingAssembly();

            // 1. Extract embedded app_icon.ico
            string iconOut = Path.Combine(installDir, "app_icon.ico");
            using (Stream icoStream = asm.GetManifestResourceStream("app_icon.ico"))
            {
                if (icoStream != null)
                {
                    try
                    {
                        using (FileStream fs = new FileStream(iconOut, FileMode.Create, FileAccess.Write))
                        {
                            icoStream.CopyTo(fs);
                        }
                    }
                    catch { }
                }
            }

            // 2. Extract embedded ui_bundle.zip
            using (Stream resStream = asm.GetManifestResourceStream("ui_bundle.zip"))
            {
                if (resStream != null)
                {
                    string tempZip = Path.Combine(Path.GetTempPath(), "liningops_ui_" + Guid.NewGuid().ToString("N") + ".zip");
                    try
                    {
                        using (FileStream fs = new FileStream(tempZip, FileMode.Create, FileAccess.Write))
                        {
                            resStream.CopyTo(fs);
                        }
                        using (ZipArchive archive = ZipFile.OpenRead(tempZip))
                        {
                            foreach (ZipArchiveEntry entry in archive.Entries)
                            {
                                if (string.IsNullOrEmpty(entry.Name))
                                    continue;
                                string destPath = Path.Combine(extractRoot, entry.FullName.Replace('/', Path.DirectorySeparatorChar));
                                string destDir = Path.GetDirectoryName(destPath);
                                if (!string.IsNullOrEmpty(destDir))
                                    Directory.CreateDirectory(destDir);
                                entry.ExtractToFile(destPath, true);
                            }
                        }
                    }
                    finally
                    {
                        if (File.Exists(tempZip))
                        {
                            try { File.Delete(tempZip); } catch { }
                        }
                    }
                    return extractRoot;
                }
            }

            string exeDir = AppDomain.CurrentDomain.BaseDirectory;
            string localUi = Path.Combine(exeDir, "ui");
            if (File.Exists(Path.Combine(localUi, "app.html")))
                return localUi;

            string parentUi = Path.GetFullPath(Path.Combine(exeDir, "..", "ui"));
            if (File.Exists(Path.Combine(parentUi, "app.html")))
                return parentUi;

            return extractRoot;
        }

        private static void CreateShortcuts(string targetExe, string iconPath, bool forceCreate)
        {
            try
            {
                string desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                string desktopLnk = Path.Combine(desktopDir, "ZeroZeta LiningOps Copilot.lnk");

                string startMenuDir = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                string startMenuLnk = Path.Combine(startMenuDir, "ZeroZeta LiningOps Copilot.lnk");

                if (!forceCreate && File.Exists(desktopLnk))
                    return;

                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null)
                    return;

                dynamic shell = Activator.CreateInstance(shellType);
                string workDir = Path.GetDirectoryName(targetExe) ?? "";
                string iconLoc = File.Exists(iconPath) ? (iconPath + ",0") : (targetExe + ",0");

                foreach (string lnkPath in new string[] { desktopLnk, startMenuLnk })
                {
                    try
                    {
                        dynamic shortcut = shell.CreateShortcut(lnkPath);
                        shortcut.TargetPath = targetExe;
                        shortcut.WorkingDirectory = workDir;
                        shortcut.Description = "ZeroZeta — LiningOps Copilot (Refractory Engineering Desktop)";
                        shortcut.IconLocation = iconLoc;
                        shortcut.Save();
                    }
                    catch { }
                }
            }
            catch { }
        }

        private static bool LaunchAppWindow(string targetUrl)
        {
            string progFilesX86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
            string progFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

            string[] candidates = new string[]
            {
                Path.Combine(progFilesX86, @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(progFiles, @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(progFiles, @"Google\Chrome\Application\chrome.exe"),
                Path.Combine(progFilesX86, @"Google\Chrome\Application\chrome.exe"),
                Path.Combine(localAppData, @"Google\Chrome\Application\chrome.exe")
            };

            foreach (string browserExe in candidates)
            {
                if (!string.IsNullOrEmpty(browserExe) && File.Exists(browserExe))
                {
                    ProcessStartInfo psi = new ProcessStartInfo
                    {
                        FileName = browserExe,
                        Arguments = "--app=\"" + targetUrl + "\" --start-maximized",
                        UseShellExecute = false
                    };
                    Process.Start(psi);
                    return true;
                }
            }
            return false;
        }
    }
}
