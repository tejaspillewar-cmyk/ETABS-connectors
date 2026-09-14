Imports System
Imports System.Diagnostics
Imports ETABSv1

Namespace PyLauncherPlugin

    ''' <summary>
    ''' ETABS plugin entry point to launch a Python script.
    ''' </summary>
    Public Class cPlugin

        ''' <summary>Called by ETABS when the plugin menu item is clicked.</summary>
        Public Sub Main(ByRef SapModel As cSapModel, ByRef ISapPlugin As cPluginCallback)
            Try
                ' Dynamically resolve the python file path relative to this plugin's DLL location
                Dim pluginFolder As String = System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location)
                Dim pythonFilePath As String = System.IO.Path.Combine(pluginFolder, "etabs_gui.pyw")                
                Dim startInfo As New ProcessStartInfo()
                ' Ensure python is in your system PATH or provide the full path to python.exe
                startInfo.FileName = "pythonw.exe"
                startInfo.Arguments = """" & pythonFilePath & """"
                startInfo.UseShellExecute = True
                
                Process.Start(startInfo)
                
            Catch ex As Exception
                ' Failsafe block in case the python script fails to launch
            End Try
            
            ' Signal ETABS that plugin finished
            If ISapPlugin IsNot Nothing Then
                ISapPlugin.Finish(0)
            End If
        End Sub

        ''' <summary>Returns plugin description text to ETABS.</summary>
        Public Function Info(ByRef Text As String) As Long
            Text = "Python Script Launcher Plugin"
            Return 0L
        End Function

    End Class

End Namespace
