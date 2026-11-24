// Chatbot Mensura.exe — el programa de Windows.
//
// Doble clic: arranca el servidor del chatbot (Python de la carpeta, sin consola), espera a
// que conteste y abre el chat en su PROPIA ventana (Microsoft Edge en modo aplicación, que
// trae todo Windows 10/11: sin barra de direcciones ni pestañas). Al cerrar la ventana se
// apaga todo, también los procesos del modelo (llama-server).
//
// Se compila con el csc.exe que trae .NET Framework 4 en cualquier Windows (C# 5: nada de
// interpolación $"" ni operadores ?.): escritorio\construir.bat. No hay que instalar nada.
//
// Argumentos:  --red        el chatbot se ve también desde otros PC de la red (no solo local)
//              --puerto N   puerto (8013 por omisión)

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Chatbot Mensura")]
[assembly: AssemblyDescription("Asistente de normativa de mensura y registro inmobiliario")]
[assembly: AssemblyCompany("Lexgeo")]
[assembly: AssemblyProduct("Chatbot Mensura")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

static class Programa
{
    const string Titulo = "Chatbot Mensura";
    static string raiz;
    static Process servidor;
    static StreamWriter registro;

    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        raiz = AppDomain.CurrentDomain.BaseDirectory;
        int puerto = 8013;
        bool red = false;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--red") red = true;
            if (args[i] == "--puerto" && i + 1 < args.Length) int.TryParse(args[i + 1], out puerto);
        }

        string python = Path.Combine(raiz, @"runtime\python\python.exe");
        string iniciar = Path.Combine(raiz, "iniciar.py");
        if (!File.Exists(python) || !File.Exists(iniciar))
        {
            MessageBox.Show("Falta el Python de la carpeta (runtime\\python).\n\n" +
                            "Ejecuta INSTALAR.bat una vez y vuelve a abrir el programa.",
                            Titulo, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }

        bool primero;
        using (var unico = new Mutex(true, "Lexgeo.ChatbotMensura." + puerto, out primero))
        {
            string url = "http://127.0.0.1:" + puerto + "/";
            // Ya hay un chatbot sirviendo en ese puerto (otra ventana, o INICIAR.bat): se usa.
            bool yaCorre = Responde(url);
            if (!yaCorre && !primero)
            {
                // Otro lanzador está arrancando: esperar a que conteste.
                yaCorre = Esperar(url, 120, null);
            }
            if (!yaCorre)
            {
                if (PuertoOcupado(puerto))
                {
                    puerto = PuertoLibre();
                    url = "http://127.0.0.1:" + puerto + "/";
                }
                if (!Arrancar(python, iniciar, puerto, red)) return 1;
                if (!EsperarConAviso(url))
                {
                    Apagar();
                    MessageBox.Show("El chatbot no llegó a arrancar.\n\nEl detalle está en " +
                                    "datos\\servidor.log.", Titulo, MessageBoxButtons.OK,
                                    MessageBoxIcon.Error);
                    return 1;
                }
            }

            DateTime abierta = DateTime.Now;
            Process ventana = AbrirVentana(url);
            if (ventana != null)
            {
                ventana.WaitForExit();
                // Si Edge «sale» enseguida es que le pasó la ventana a otro Edge con el mismo
                // perfil (revisión, hallazgo 12): la ventana sigue abierta. Entonces no se
                // apaga nada: el servidor se apaga solo cuando ninguna pantalla lo usa
                // (--apagar-sin-uso), y aquí se espera a eso.
                if (!yaCorre && (DateTime.Now - abierta).TotalSeconds < 8)
                {
                    servidor.WaitForExit();
                }
            }
            else
            {
                // Sin Edge: se abre el navegador de siempre y un aviso hace de interruptor.
                Process.Start(url);
                MessageBox.Show("El chatbot está abierto en el navegador.\n\n" +
                                "Pulsa Aceptar para APAGARLO cuando termines.",
                                Titulo, MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            if (!yaCorre) Apagar();
        }
        return 0;
    }

    // ------------------------------------------------------------------ servidor

    static bool Arrancar(string python, string iniciar, int puerto, bool red)
    {
        try
        {
            string datos = Path.Combine(raiz, "datos");
            Directory.CreateDirectory(datos);
            registro = new StreamWriter(Path.Combine(datos, "servidor.log"), true);
            registro.AutoFlush = true;
            registro.WriteLine("---- " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") +
                               " arranque desde Chatbot Mensura.exe");

            // --apagar-sin-uso: si ninguna ventana lo usa en 3 minutos, el servidor se apaga solo.
            // La pantalla pide /api/salud cada minuto; sin ventanas, nadie pregunta.
            var psi = new ProcessStartInfo(python, "\"" + iniciar + "\" --puerto " + puerto +
                                           " --host " + (red ? "0.0.0.0" : "127.0.0.1") +
                                           (red ? "" : " --apagar-sin-uso 180"));
            psi.WorkingDirectory = raiz;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.EnvironmentVariables["PYTHONPATH"] = Path.Combine(raiz, "libs");
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            servidor = new Process();
            servidor.StartInfo = psi;
            servidor.OutputDataReceived += Anotar;
            servidor.ErrorDataReceived += Anotar;
            servidor.Start();
            AtarAEsteProceso(servidor);
            servidor.BeginOutputReadLine();
            servidor.BeginErrorReadLine();
            return true;
        }
        catch (Exception e)
        {
            MessageBox.Show("No pude arrancar el chatbot:\n\n" + e.Message, Titulo,
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return false;
        }
    }

    static void Anotar(object quien, DataReceivedEventArgs e)
    {
        if (e.Data == null || registro == null) return;
        lock (registro) { registro.WriteLine(e.Data); }
    }

    static void Apagar()
    {
        // El servidor de Python arranca los llama-server: se mata el ÁRBOL entero (/T), o el
        // modelo se quedaría ocupando la tarjeta gráfica sin nadie que lo use.
        if (servidor == null) return;
        try
        {
            if (!servidor.HasExited)
            {
                var psi = new ProcessStartInfo("taskkill", "/PID " + servidor.Id + " /T /F");
                psi.CreateNoWindow = true;
                psi.UseShellExecute = false;
                Process.Start(psi).WaitForExit(15000);
            }
        }
        catch (Exception) { }
    }

    static bool Responde(string url)
    {
        // /api/ping es instantáneo; /api/salud consulta los llama-server y puede tardar más
        // que la espera, lo que hacía creer que no había servidor y arrancar un segundo.
        try
        {
            var peticion = (HttpWebRequest)WebRequest.Create(url + "api/ping");
            peticion.Timeout = 3000;
            peticion.Proxy = null;
            using (var r = (HttpWebResponse)peticion.GetResponse())
            using (var lector = new StreamReader(r.GetResponseStream()))
            {
                return lector.ReadToEnd().Contains("\"mensura\"");
            }
        }
        catch (Exception) { return false; }
    }

    // ------------------------------------------------------- atar el servidor al programa

    [StructLayout(LayoutKind.Sequential)]
    struct LimitesBasicos
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ContadoresIo
    {
        public ulong Lecturas, Escrituras, Otras, BytesLeidos, BytesEscritos, OtrosBytes;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct LimitesExtendidos
    {
        public LimitesBasicos Basicos;
        public ContadoresIo Io;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateJobObject(IntPtr atributos, string nombre);
    [DllImport("kernel32.dll")]
    static extern bool SetInformationJobObject(IntPtr trabajo, int clase, IntPtr info, uint largo);
    [DllImport("kernel32.dll")]
    static extern bool AssignProcessToJobObject(IntPtr trabajo, IntPtr proceso);

    static IntPtr trabajo = IntPtr.Zero;

    static void AtarAEsteProceso(Process p)
    {
        // «Job Object» con KILL_ON_JOB_CLOSE: si este programa muere (Administrador de
        // tareas, cuelgue), Windows cierra el servidor y, en cadena, los procesos del modelo.
        // Revisión del 24-nov-2026, hallazgo 12a: sin esto quedaban vivos para siempre.
        try
        {
            if (trabajo == IntPtr.Zero)
            {
                trabajo = CreateJobObject(IntPtr.Zero, null);
                var info = new LimitesExtendidos();
                info.Basicos.LimitFlags = 0x2000;   // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                int largo = Marshal.SizeOf(typeof(LimitesExtendidos));
                IntPtr ptr = Marshal.AllocHGlobal(largo);
                Marshal.StructureToPtr(info, ptr, false);
                SetInformationJobObject(trabajo, 9, ptr, (uint)largo);
                Marshal.FreeHGlobal(ptr);
            }
            AssignProcessToJobObject(trabajo, p.Handle);
        }
        catch (Exception) { }
    }

    static bool Esperar(string url, int segundos, Func<bool> cancelado)
    {
        DateTime limite = DateTime.Now.AddSeconds(segundos);
        while (DateTime.Now < limite)
        {
            if (Responde(url)) return true;
            if (servidor != null && servidor.HasExited) return false;
            if (cancelado != null && cancelado()) return false;
            Thread.Sleep(400);
        }
        return false;
    }

    static bool EsperarConAviso(string url)
    {
        // Una ventanita mientras carga: la primera vez el índice tarda unos segundos.
        var aviso = new Form();
        aviso.Text = Titulo;
        aviso.FormBorderStyle = FormBorderStyle.FixedDialog;
        aviso.ControlBox = false;
        aviso.StartPosition = FormStartPosition.CenterScreen;
        aviso.ClientSize = new Size(360, 120);
        aviso.BackColor = Color.White;
        aviso.ShowInTaskbar = true;
        try { aviso.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }
        var texto = new Label();
        texto.Text = "Preparando el Chatbot Mensura…\n\nCargando la normativa y el modelo local.";
        texto.Font = new Font("Segoe UI", 10f);
        texto.ForeColor = Color.FromArgb(29, 29, 31);
        texto.AutoSize = false;
        texto.SetBounds(20, 16, 320, 60);
        var barra = new ProgressBar();
        barra.Style = ProgressBarStyle.Marquee;
        barra.SetBounds(20, 84, 320, 14);
        aviso.Controls.Add(texto);
        aviso.Controls.Add(barra);

        bool listo = false;
        var hilo = new Thread(() =>
        {
            listo = Esperar(url, 180, null);
            try { aviso.Invoke((MethodInvoker)delegate { aviso.Close(); }); } catch (Exception) { }
        });
        hilo.IsBackground = true;
        aviso.Shown += delegate { hilo.Start(); };
        Application.Run(aviso);
        return listo;
    }

    static bool PuertoOcupado(int puerto)
    {
        try
        {
            var l = new TcpListener(IPAddress.Loopback, puerto);
            l.Start();
            l.Stop();
            return false;
        }
        catch (SocketException) { return true; }
    }

    static int PuertoLibre()
    {
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        int p = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return p;
    }

    // ------------------------------------------------------------------- ventana

    static string BuscarEdge()
    {
        string[] claves = {
            @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
            @"HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
        };
        foreach (string clave in claves)
        {
            try
            {
                string ruta = Registry.GetValue(clave, "", null) as string;
                if (!string.IsNullOrEmpty(ruta) && File.Exists(ruta)) return ruta;
            }
            catch (Exception) { }
        }
        string[] rutas = {
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86) +
                @"\Microsoft\Edge\Application\msedge.exe",
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) +
                @"\Microsoft\Edge\Application\msedge.exe",
        };
        foreach (string ruta in rutas) if (File.Exists(ruta)) return ruta;
        return null;
    }

    static Process AbrirVentana(string url)
    {
        string edge = BuscarEdge();
        if (edge == null) return null;
        // Perfil PROPIO del chatbot: así la ventana es un proceso aparte (se sabe cuándo se
        // cierra) y no se mezcla con las pestañas ni las cuentas del Edge de la persona.
        string perfil = Path.Combine(Environment.GetFolderPath(
            Environment.SpecialFolder.LocalApplicationData), @"Lexgeo\ChatbotMensura\ventana");
        Directory.CreateDirectory(perfil);
        string args = "--app=" + url + " --user-data-dir=\"" + perfil + "\"" +
                      " --no-first-run --no-default-browser-check --disable-sync" +
                      " --disable-background-mode" +
                      " --disable-features=Translate,msEdgeSidebarV2 --window-size=1180,860";
        try
        {
            return Process.Start(edge, args);
        }
        catch (Exception) { return null; }
    }
}
