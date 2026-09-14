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
                ' Get the folder where the DLL is located
                Dim pluginFolder As String = System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location)
                
                ' Look ONE folder up for the python file using ".."
                Dim pythonFilePath As String = System.IO.Path.GetFullPath(System.IO.Path.Combine(pluginFolder, "..", "etabs_gui.pyw"))
                
                ' Check if the file actually exists before trying to run it
                If Not System.IO.File.Exists(pythonFilePath) Then
                    System.Windows.Forms.MessageBox.Show("Could not find the Python script at:" & vbCrLf & pythonFilePath, "File Not Found", System.Windows.Forms.MessageBoxButtons.OK, System.Windows.Forms.MessageBoxIcon.Error)
                    Return
                End If
                
                Dim startInfo As New ProcessStartInfo()
                startInfo.FileName = "pythonw.exe"
                startInfo.Arguments = """" & pythonFilePath & """"
                startInfo.UseShellExecute = False
                startInfo.CreateNoWindow = True
                
                Process.Start(startInfo)
                
            Catch ex As Exception
                System.Windows.Forms.MessageBox.Show("Failed to launch Python script: " & ex.Message, "Plugin Error", System.Windows.Forms.MessageBoxButtons.OK, System.Windows.Forms.MessageBoxIcon.Error)
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
