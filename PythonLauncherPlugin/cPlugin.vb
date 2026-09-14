Imports System
Imports System.Diagnostics
Imports ETABSv1

Namespace PythonLauncherPlugin

    ''' <summary>
    ''' ETABS plugin entry point.
    ''' This class is exposed to COM and launched by ETABS when the plugin is clicked.
    ''' </summary>
    Public Class cPlugin

        ''' <summary>Called by ETABS when the plugin menu item is clicked.</summary>
        Public Sub Main(ByRef SapModel As cSapModel, ByRef ISapPlugin As cPluginCallback)
            Try
                ' This is the location of the Python script to be executed.
                ' Replace this string with the actual path to your Python file.
                Dim pythonFilePath As String = "C:\Path\To\Your\Script.py"
                
                ' Setup the process to run the python file
                Dim startInfo As New ProcessStartInfo()
                startInfo.FileName = "python.exe" ' Make sure python is in your system PATH, or provide full path
                startInfo.Arguments = $"""{pythonFilePath}"""
                startInfo.UseShellExecute = True
                
                ' Start the Python script
                Process.Start(startInfo)
                
            Catch ex As Exception
                ' Failsafe block in case the python script fails to launch
                ' For debugging, you could show a MessageBox here:
                ' System.Windows.MessageBox.Show(ex.Message, "Python Plugin Error")
            End Try
            
            ' Signal ETABS that the plugin finished executing.
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
