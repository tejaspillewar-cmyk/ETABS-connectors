// ETABS "Add/Show Plugins" entry point.
//
// ETABS requires a class literally named `cPlugin` implementing
// ETABSv1.cPluginContract (see "Information for Plugin Developers" in
// CSI API ETABS v1.chm). Main() is called on ETABS's UI thread with a live
// SapModel for *this* instance -- we don't hand that COM pointer to Python
// (marshaling it across processes isn't worth the complexity here). Instead
// we grab our own process id, which is the ETABS.exe that loaded us, and
// pass it to the GUI as --pid so it can attach with
// helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", pid) -- the same call
// etabs_gui.pyw's "Attach" button already uses, just aimed at a known PID
// instead of guessing when multiple ETABS windows are open.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
using ETABSv1;

// Compiled with the .NET Framework 4.0 csc.exe (no VS/SDK needed on this
// machine), but 4.0-4.8 share one CLR (v4.0.30319), so stamping 4.8 here
// gives ETABS's loader an accurate compatibility marker for a plugin that
// only uses APIs present since 4.0.
[assembly: System.Runtime.Versioning.TargetFramework(
    ".NETFramework,Version=v4.8", FrameworkDisplayName = ".NET Framework 4.8")]

public class cPlugin : cPluginContract
{
    public int Info(ref string Text)
    {
        Text = "ETABS Live Connector\r\n\r\n"
             + "Opens the external Python control panel (pier extraction, "
             + "FDR tool, CAD/Excel export) and attaches it to this ETABS "
             + "instance.";
        return 0;
    }

    public void Main(ref cSapModel SapModel, ref cPluginCallback ISapPlugin)
    {
        int errorFlag = 0;
        try
        {
            string pluginDir = Path.GetDirectoryName(
                System.Reflection.Assembly.GetExecutingAssembly().Location);
            var config = ReadConfig(Path.Combine(pluginDir, "plugin.config"));

            string pythonw = config.ContainsKey("PYTHONW") ? config["PYTHONW"] : "";
            string script = config.ContainsKey("SCRIPT") ? config["SCRIPT"] : "";

            if (string.IsNullOrEmpty(script) || !File.Exists(script))
                throw new FileNotFoundException(
                    "etabs_gui.pyw not found. Edit plugin.config next to " +
                    "EtabsGuiPlugin.dll and set SCRIPT= to its full path. " +
                    "Looked for: " + script);

            if (string.IsNullOrEmpty(pythonw) || !File.Exists(pythonw))
                throw new FileNotFoundException(
                    "pythonw.exe not found. Edit plugin.config and set PYTHONW= " +
                    "to the interpreter with comtypes/psutil/pandas/openpyxl/ezdxf " +
                    "installed. Looked for: " + pythonw);

            int pid = Process.GetCurrentProcess().Id;

            var psi = new ProcessStartInfo
            {
                FileName = pythonw,
                Arguments = "\"" + script + "\" --pid " + pid,
                WorkingDirectory = Path.GetDirectoryName(script),
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            errorFlag = 1;
            MessageBox.Show(
                "Failed to launch ETABS Live Connector:\r\n\r\n" + ex.Message,
                "ETABS Live Connector", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally
        {
            // The GUI is a separate process from here on; ETABS only needed
            // to stay blocked long enough for us to hand off the PID.
            ISapPlugin.Finish(errorFlag);
        }
    }

    private static Dictionary<string, string> ReadConfig(string path)
    {
        var result = new Dictionary<string, string>();
        if (!File.Exists(path))
            return result;

        foreach (var rawLine in File.ReadAllLines(path))
        {
            string line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith("#"))
                continue;
            int eq = line.IndexOf('=');
            if (eq < 0)
                continue;
            string key = line.Substring(0, eq).Trim().ToUpperInvariant();
            string value = line.Substring(eq + 1).Trim();
            result[key] = value;
        }
        return result;
    }
}
