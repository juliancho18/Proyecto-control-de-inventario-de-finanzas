from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import os
import requests
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
app.secret_key = os.getenv("SECRET_KEY", "clave_desarrollo")

DB_PATH = "instance/finanzas.db"

USUARIO_SISTEMA = "angi"
PASSWORD_INICIAL = "tatiana"
CLAVE_DESBLOQUEO = "2026"


def get_connection():
    os.makedirs("instance", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS prestamos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            valor_prestado REAL NOT NULL,
            meses INTEGER NOT NULL,
            interes REAL NOT NULL,
            dia_pago INTEGER NOT NULL,
            fecha_registro TEXT NOT NULL,
            valor_mensual_base REAL NOT NULL,
            total_con_interes REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cuotas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prestamo_id INTEGER NOT NULL,
            numero_cuota INTEGER NOT NULL,
            fecha_pago TEXT NOT NULL,
            valor_base REAL NOT NULL,
            incremento_mora REAL DEFAULT 0,
            valor_total REAL NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            fecha_pagada TEXT,
            FOREIGN KEY(prestamo_id) REFERENCES prestamos(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_servicio TEXT NOT NULL,
            proveedor TEXT NOT NULL,
            valor_total REAL NOT NULL,
            meses INTEGER NOT NULL,
            dia_pago INTEGER NOT NULL,
            fecha_registro TEXT NOT NULL,
            valor_mensual REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cuotas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            servicio_id INTEGER NOT NULL,
            numero_cuota INTEGER NOT NULL,
            fecha_pago TEXT NOT NULL,
            valor_base REAL NOT NULL,
            incremento_mora REAL DEFAULT 0,
            valor_total REAL NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            fecha_pagada TEXT,
            FOREIGN KEY(servicio_id) REFERENCES servicios(id)
        )
    """)

    conn.commit()
    conn.close()


def init_auth_session():
    if "password_actual" not in session:
        session["password_actual"] = PASSWORD_INICIAL
    if "intentos_fallidos" not in session:
        session["intentos_fallidos"] = 0
    if "cuenta_bloqueada" not in session:
        session["cuenta_bloqueada"] = False


def login_required():
    return session.get("usuario") == USUARIO_SISTEMA


def fecha(fecha_str):
    return datetime.strptime(fecha_str, "%Y-%m-%d").date()


def estado_item(item):
    hoy = date.today()
    fecha_pago = fecha(item["fecha_pago"])

    if item["estado"] == "pagado":
        return "Pagado", "green", 0

    dias = (fecha_pago - hoy).days

    if dias < 0:
        return "Vencido", "red", abs(dias)

    if dias <= 2:
        return "Próximo a vencer", "yellow", 0

    return "Pendiente", "blue", 0


def estado_servicio_mensual(dia_pago):
    hoy = date.today()
    dia_actual = hoy.day
    dia_pago = int(dia_pago)

    dias_restantes = dia_pago - dia_actual

    if dias_restantes > 2:
        return "Al día", "green", dias_restantes

    if dias_restantes in [1, 2]:
        return "Por vencer", "yellow", dias_restantes

    return "Vencido", "red", dias_restantes


def proxima_fecha_servicio(dia_pago):
    hoy = date.today()
    dia_pago = min(int(dia_pago), 28)
    return date(hoy.year, hoy.month, dia_pago)


def crear_cuotas(tabla, id_columna, id_valor, meses, fecha_registro, dia_pago, valor_mensual):
    conn = get_connection()
    fecha_base = fecha(fecha_registro)

    for i in range(1, meses + 1):
        fecha_cuota = fecha_base + relativedelta(months=i)
        fecha_cuota = fecha_cuota.replace(day=min(dia_pago, 28))

        conn.execute(f"""
            INSERT INTO {tabla}
            ({id_columna}, numero_cuota, fecha_pago, valor_base, incremento_mora, valor_total, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            id_valor,
            i,
            fecha_cuota.strftime("%Y-%m-%d"),
            valor_mensual,
            0,
            valor_mensual,
            "pendiente"
        ))

    conn.commit()
    conn.close()


def enviar_correo(asunto, mensaje_html, mensaje_texto=None):
    destino = os.getenv("EMAIL_DESTINO")
    email_api_url = os.getenv("EMAIL_API_URL")
    email_api_token = os.getenv("EMAIL_API_TOKEN")

    if not destino:
        raise ValueError("Falta la variable EMAIL_DESTINO en Render.")

    if not email_api_url:
        raise ValueError("Falta la variable EMAIL_API_URL en Render.")

    if not email_api_token:
        raise ValueError("Falta la variable EMAIL_API_TOKEN en Render.")

    if not mensaje_texto:
        mensaje_texto = "Tienes alertas pendientes en el sistema Control Angi Tatiana."

    payload = {
        "token": email_api_token,
        "to": destino,
        "subject": asunto,
        "text": mensaje_texto,
        "html": mensaje_html
    }

    try:
        respuesta = requests.post(email_api_url, json=payload, timeout=20)

        print("RESPUESTA_EMAIL_API_STATUS:", respuesta.status_code)
        print("RESPUESTA_EMAIL_API_TEXT:", respuesta.text)

        if respuesta.status_code != 200:
            raise Exception(f"Error HTTP enviando correo por API: {respuesta.status_code} - {respuesta.text}")

        try:
            data = respuesta.json()
        except Exception:
            raise Exception(f"La API de correo no devolvió JSON válido: {respuesta.text}")

        if not data.get("ok"):
            raise Exception(f"Error en Google Apps Script: {data}")

        print("CORREO_ENVIADO_OK")
        return True

    except requests.exceptions.RequestException as error:
        print("ERROR_CONECTANDO_EMAIL_API:", error)
        raise

    except Exception as error:
        print("ERROR_ENVIANDO_CORREO:", error)
        raise


def obtener_alertas_vencimientos():
    hoy = date.today()
    dias_alerta = int(os.getenv("NOTIFICAR_DIAS", "3"))

    alertas_prestamos = []
    alertas_servicios = []

    conn = get_connection()

    cuotas = conn.execute("""
        SELECT 
            c.id,
            c.numero_cuota,
            c.fecha_pago,
            c.valor_total,
            c.estado,
            p.nombre
        FROM cuotas c
        INNER JOIN prestamos p ON p.id = c.prestamo_id
        WHERE c.estado != 'pagado'
        ORDER BY c.fecha_pago ASC
    """).fetchall()

    servicios = conn.execute("""
        SELECT *
        FROM servicios
        ORDER BY dia_pago ASC
    """).fetchall()

    conn.close()

    for c in cuotas:
        fecha_pago = fecha(c["fecha_pago"])
        dias = (fecha_pago - hoy).days

        if dias <= dias_alerta:
            if dias > 0:
                estado = f"Vence en {dias} día(s)"
            elif dias == 0:
                estado = "Vence hoy"
            else:
                estado = f"Vencido hace {abs(dias)} día(s)"

            alertas_prestamos.append({
                "nombre": c["nombre"],
                "cuota": c["numero_cuota"],
                "fecha_pago": c["fecha_pago"],
                "valor": c["valor_total"],
                "estado": estado
            })

    for s in servicios:
        semaforo, color, dias = estado_servicio_mensual(s["dia_pago"])

        if dias <= dias_alerta:
            if dias > 0:
                estado = f"Vence en {dias} día(s)"
            elif dias == 0:
                estado = "Vence hoy"
            else:
                estado = f"Vencido hace {abs(dias)} día(s)"

            alertas_servicios.append({
                "nombre_servicio": s["nombre_servicio"],
                "proveedor": s["proveedor"],
                "dia_pago": s["dia_pago"],
                "valor_mensual": s["valor_mensual"],
                "estado": estado
            })

    return alertas_prestamos, alertas_servicios


def construir_correo_alertas(alertas_prestamos, alertas_servicios):
    filas_prestamos = ""
    filas_servicios = ""

    for p in alertas_prestamos:
        filas_prestamos += f"""
        <tr>
            <td>{p['nombre']}</td>
            <td>Cuota {p['cuota']}</td>
            <td>{p['fecha_pago']}</td>
            <td>${p['valor']:,.0f}</td>
            <td><strong>{p['estado']}</strong></td>
        </tr>
        """

    for s in alertas_servicios:
        filas_servicios += f"""
        <tr>
            <td>{s['nombre_servicio']}</td>
            <td>{s['proveedor']}</td>
            <td>Día {s['dia_pago']}</td>
            <td>${s['valor_mensual']:,.0f}</td>
            <td><strong>{s['estado']}</strong></td>
        </tr>
        """

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background:#020617; color:#e5e7eb; padding:24px;">
        <div style="max-width:850px; margin:auto; background:#0f172a; padding:24px; border-radius:18px; border:1px solid #38bdf8;">
            <h1 style="color:#38bdf8;">📌 Alertas Control Angi Tatiana</h1>
            <p>Se detectaron elementos próximos a vencer o vencidos.</p>

            <h2 style="color:#facc15;">💰 Préstamos</h2>
            <table width="100%" cellpadding="10" cellspacing="0" style="border-collapse:collapse;">
                <thead>
                    <tr style="background:#1e293b;">
                        <th>Persona</th>
                        <th>Cuota</th>
                        <th>Fecha</th>
                        <th>Valor</th>
                        <th>Estado</th>
                    </tr>
                </thead>
                <tbody>
                    {filas_prestamos if filas_prestamos else '<tr><td colspan="5">Sin alertas de préstamos.</td></tr>'}
                </tbody>
            </table>

            <h2 style="color:#38bdf8; margin-top:28px;">🧾 Servicios</h2>
            <table width="100%" cellpadding="10" cellspacing="0" style="border-collapse:collapse;">
                <thead>
                    <tr style="background:#1e293b;">
                        <th>Servicio</th>
                        <th>Proveedor</th>
                        <th>Día pago</th>
                        <th>Valor</th>
                        <th>Estado</th>
                    </tr>
                </thead>
                <tbody>
                    {filas_servicios if filas_servicios else '<tr><td colspan="5">Sin alertas de servicios.</td></tr>'}
                </tbody>
            </table>

            <p style="margin-top:28px; color:#94a3b8;">
                Sistema privado de recordatorios financieros.
            </p>
        </div>
    </body>
    </html>
    """

    texto = "Alertas Control Angi Tatiana. Revisa préstamos y servicios próximos a vencer."

    return html, texto


@app.route("/", methods=["GET", "POST"])
def login():
    init_auth_session()

    if request.method == "POST":
        usuario = request.form["usuario"]
        password = request.form["password"]

        if session.get("cuenta_bloqueada"):
            flash("Cuenta bloqueada. Ingresa la clave temporal para desbloquear.")
            return redirect(url_for("desbloquear"))

        if usuario == USUARIO_SISTEMA and password == session.get("password_actual"):
            session["usuario"] = USUARIO_SISTEMA
            session["intentos_fallidos"] = 0
            flash("Ingreso exitoso.")
            return redirect(url_for("menu"))

        session["intentos_fallidos"] += 1
        intentos_restantes = 4 - session["intentos_fallidos"]

        if session["intentos_fallidos"] >= 4:
            session["cuenta_bloqueada"] = True
            flash("Cuenta bloqueada por demasiados intentos fallidos.")
            return redirect(url_for("desbloquear"))

        flash(f"Usuario o contraseña incorrectos. Intentos restantes: {intentos_restantes}")

    return render_template("login.html")


@app.route("/desbloquear", methods=["GET", "POST"])
def desbloquear():
    init_auth_session()

    if request.method == "POST":
        clave_temporal = request.form["clave_temporal"]

        if clave_temporal == CLAVE_DESBLOQUEO:
            session["cuenta_bloqueada"] = False
            session["intentos_fallidos"] = 0
            flash("Cuenta desbloqueada correctamente.")
            return redirect(url_for("login"))

        flash("Clave temporal incorrecta.")

    return render_template("desbloquear.html")


@app.route("/cambiar_password", methods=["GET", "POST"])
def cambiar_password():
    if not login_required():
        return redirect(url_for("login"))

    init_auth_session()

    if request.method == "POST":
        password_anterior = request.form["password_anterior"]
        password_nueva = request.form["password_nueva"]
        confirmar_password = request.form["confirmar_password"]

        if password_anterior != session.get("password_actual"):
            flash("La contraseña anterior no es correcta.")
            return redirect(url_for("cambiar_password"))

        if password_nueva != confirmar_password:
            flash("Las contraseñas nuevas no coinciden.")
            return redirect(url_for("cambiar_password"))

        session["password_actual"] = password_nueva
        flash("Contraseña actualizada correctamente.")
        return redirect(url_for("menu"))

    return render_template("cambiar_password.html")


@app.route("/logout")
def logout():
    password_actual = session.get("password_actual", PASSWORD_INICIAL)
    cuenta_bloqueada = session.get("cuenta_bloqueada", False)
    intentos_fallidos = session.get("intentos_fallidos", 0)

    session.clear()

    session["password_actual"] = password_actual
    session["cuenta_bloqueada"] = cuenta_bloqueada
    session["intentos_fallidos"] = intentos_fallidos

    return redirect(url_for("login"))


@app.route("/menu")
def menu():
    if not login_required():
        return redirect(url_for("login"))

    return render_template("menu.html")


@app.route("/prestamos")
def prestamos():
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()
    prestamos_raw = conn.execute("SELECT * FROM prestamos ORDER BY id DESC").fetchall()

    lista = []

    for p in prestamos_raw:
        cuotas = conn.execute(
            "SELECT * FROM cuotas WHERE prestamo_id = ? ORDER BY numero_cuota",
            (p["id"],)
        ).fetchall()

        pagadas = sum(1 for c in cuotas if c["estado"] == "pagado")
        vencidas = 0
        deuda_actual = 0

        for c in cuotas:
            estado_texto, color, dias = estado_item(c)

            if c["estado"] != "pagado":
                deuda_actual += c["valor_total"]

            if estado_texto == "Vencido":
                vencidas += 1

        pendiente = next((c for c in cuotas if c["estado"] != "pagado"), None)

        if pendiente:
            semaforo, color, _ = estado_item(pendiente)
            proxima_fecha = pendiente["fecha_pago"]
        else:
            semaforo, color, proxima_fecha = "Pagado", "green", "Finalizado"

        item = dict(p)
        item["cuotas_pagadas"] = pagadas
        item["cuotas_vencidas"] = vencidas
        item["deuda_actual"] = deuda_actual
        item["proxima_fecha"] = proxima_fecha
        item["semaforo"] = semaforo
        item["color"] = color

        lista.append(item)

    conn.close()

    return render_template(
        "prestamos.html",
        prestamos=lista,
        total_prestado=sum(p["valor_prestado"] for p in lista),
        total_mensual=sum(p["valor_mensual_base"] for p in lista),
        total_con_interes=sum(p["total_con_interes"] for p in lista),
        total_personas=len(lista)
    )


@app.route("/crear_prestamo", methods=["POST"])
def crear_prestamo():
    if not login_required():
        return redirect(url_for("login"))

    nombre = request.form["nombre"]
    valor_prestado = float(request.form["valor_prestado"])
    meses = int(request.form["meses"])
    interes = float(request.form["interes"])
    dia_pago = int(request.form["dia_pago"])
    fecha_registro = request.form["fecha_registro"]

    total_con_interes = valor_prestado + (valor_prestado * interes / 100)
    valor_mensual = total_con_interes / meses

    conn = get_connection()

    cursor = conn.execute("""
        INSERT INTO prestamos
        (nombre, valor_prestado, meses, interes, dia_pago, fecha_registro, valor_mensual_base, total_con_interes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        nombre,
        valor_prestado,
        meses,
        interes,
        dia_pago,
        fecha_registro,
        valor_mensual,
        total_con_interes
    ))

    prestamo_id = cursor.lastrowid
    conn.commit()
    conn.close()

    crear_cuotas("cuotas", "prestamo_id", prestamo_id, meses, fecha_registro, dia_pago, valor_mensual)

    flash("Préstamo creado correctamente.")
    return redirect(url_for("prestamos"))


@app.route("/detalle_prestamo/<int:id>")
def detalle_prestamo(id):
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()

    prestamo = conn.execute(
        "SELECT * FROM prestamos WHERE id = ?",
        (id,)
    ).fetchone()

    cuotas_raw = conn.execute(
        "SELECT * FROM cuotas WHERE prestamo_id = ? ORDER BY numero_cuota",
        (id,)
    ).fetchall()

    conn.close()

    cuotas = []

    for c in cuotas_raw:
        estado_texto, color, dias = estado_item(c)
        item = dict(c)
        item["estado_texto"] = estado_texto
        item["color"] = color
        item["dias_vencidos"] = dias
        cuotas.append(item)

    return render_template(
        "detalle_prestamo.html",
        prestamo=prestamo,
        cuotas=cuotas
    )


@app.route("/servicios")
def servicios():
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()
    servicios_raw = conn.execute(
        "SELECT * FROM servicios ORDER BY dia_pago ASC"
    ).fetchall()
    conn.close()

    lista = []
    servicios_vencidos = 0
    servicios_por_vencer = 0

    for s in servicios_raw:
        item = dict(s)

        semaforo, color, dias_restantes = estado_servicio_mensual(item["dia_pago"])
        proxima_fecha = proxima_fecha_servicio(item["dia_pago"])

        item["semaforo"] = semaforo
        item["color"] = color
        item["dias_restantes"] = dias_restantes
        item["proxima_fecha"] = proxima_fecha.strftime("%Y-%m-%d")

        if color == "red":
            servicios_vencidos += 1

        if color == "yellow":
            servicios_por_vencer += 1

        lista.append(item)

    return render_template(
        "servicios.html",
        servicios=lista,
        cantidad_servicios=len(lista),
        total_mensual=sum(s["valor_mensual"] for s in lista),
        servicios_vencidos=servicios_vencidos,
        servicios_por_vencer=servicios_por_vencer
    )


@app.route("/crear_servicio", methods=["POST"])
def crear_servicio():
    if not login_required():
        return redirect(url_for("login"))

    nombre_servicio = request.form["nombre_servicio"]
    proveedor = request.form["proveedor"]
    valor_mensual = float(request.form["valor_mensual"])
    dia_pago = int(request.form["dia_pago"])
    fecha_registro = date.today().strftime("%Y-%m-%d")

    conn = get_connection()

    conn.execute("""
        INSERT INTO servicios
        (nombre_servicio, proveedor, valor_total, meses, dia_pago, fecha_registro, valor_mensual)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        nombre_servicio,
        proveedor,
        valor_mensual,
        1,
        dia_pago,
        fecha_registro,
        valor_mensual
    ))

    conn.commit()
    conn.close()

    flash("Servicio mensual creado correctamente.")
    return redirect(url_for("servicios"))


@app.route("/detalle_servicio/<int:id>")
def detalle_servicio(id):
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()

    servicio_raw = conn.execute(
        "SELECT * FROM servicios WHERE id = ?",
        (id,)
    ).fetchone()

    conn.close()

    if not servicio_raw:
        flash("Servicio no encontrado.")
        return redirect(url_for("servicios"))

    servicio = dict(servicio_raw)

    semaforo, color, dias_restantes = estado_servicio_mensual(servicio["dia_pago"])
    proxima_fecha = proxima_fecha_servicio(servicio["dia_pago"])

    servicio["semaforo"] = semaforo
    servicio["color"] = color
    servicio["dias_restantes"] = dias_restantes
    servicio["proxima_fecha"] = proxima_fecha.strftime("%Y-%m-%d")

    return render_template(
        "detalle_servicio.html",
        servicio=servicio
    )


@app.route("/pagar/<tipo>/<int:id>")
def pagar(tipo, id):
    if not login_required():
        return redirect(url_for("login"))

    if tipo != "prestamo":
        flash("Los servicios mensuales no manejan cuotas individuales.")
        return redirect(url_for("servicios"))

    conn = get_connection()

    conn.execute("""
        UPDATE cuotas
        SET estado = 'pagado', fecha_pagada = ?
        WHERE id = ?
    """, (
        date.today().strftime("%Y-%m-%d"),
        id
    ))

    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("menu"))


@app.route("/editar_cuota/<tipo>/<int:id>", methods=["POST"])
def editar_cuota(tipo, id):
    if not login_required():
        return redirect(url_for("login"))

    if tipo != "prestamo":
        flash("Los servicios mensuales no manejan cuotas individuales.")
        return redirect(url_for("servicios"))

    fecha_pago = request.form["fecha_pago"]
    incremento_mora = float(request.form["incremento_mora"])
    estado = request.form["estado"]

    conn = get_connection()

    cuota = conn.execute(
        "SELECT * FROM cuotas WHERE id = ?",
        (id,)
    ).fetchone()

    if not cuota:
        conn.close()
        flash("Cuota no encontrada.")
        return redirect(url_for("menu"))

    valor_total = cuota["valor_base"] + incremento_mora

    conn.execute("""
        UPDATE cuotas
        SET fecha_pago = ?, incremento_mora = ?, valor_total = ?, estado = ?
        WHERE id = ?
    """, (
        fecha_pago,
        incremento_mora,
        valor_total,
        estado,
        id
    ))

    conn.commit()
    conn.close()

    flash("Cuota actualizada correctamente.")
    return redirect(request.referrer or url_for("menu"))


@app.route("/borrar/<tipo>/<int:id>", methods=["POST"])
def borrar(tipo, id):
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()

    if tipo == "prestamo":
        conn.execute("DELETE FROM cuotas WHERE prestamo_id = ?", (id,))
        conn.execute("DELETE FROM prestamos WHERE id = ?", (id,))
        destino = "prestamos"

    elif tipo == "servicio":
        conn.execute("DELETE FROM cuotas_servicios WHERE servicio_id = ?", (id,))
        conn.execute("DELETE FROM servicios WHERE id = ?", (id,))
        destino = "servicios"

    else:
        conn.close()
        flash("Tipo de registro no válido.")
        return redirect(url_for("menu"))

    conn.commit()
    conn.close()

    flash("Registro eliminado correctamente.")
    return redirect(url_for(destino))


@app.route("/dashboard")
def dashboard():
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()

    prestamos_db = conn.execute("SELECT * FROM prestamos").fetchall()
    servicios_db = conn.execute("SELECT * FROM servicios ORDER BY dia_pago ASC").fetchall()
    cuotas_db = conn.execute("SELECT * FROM cuotas").fetchall()

    conn.close()

    total_prestamos = sum(p["valor_prestado"] for p in prestamos_db)
    total_servicios = sum(s["valor_mensual"] for s in servicios_db)

    cuotas_vencidas = 0
    cuotas_proximas = 0
    cuotas_pendientes = 0
    cuotas_pagadas = 0

    for c in cuotas_db:
        estado_texto, _, _ = estado_item(c)

        if c["estado"] == "pagado":
            cuotas_pagadas += 1
        elif estado_texto == "Vencido":
            cuotas_vencidas += 1
        elif estado_texto == "Próximo a vencer":
            cuotas_proximas += 1
        else:
            cuotas_pendientes += 1

    servicios_vencidos = 0
    servicios_por_vencer = 0
    servicios_lista = []

    for s in servicios_db:
        semaforo, color, dias_restantes = estado_servicio_mensual(s["dia_pago"])

        if color == "red":
            servicios_vencidos += 1

        if color == "yellow":
            servicios_por_vencer += 1

        servicios_lista.append({
            "nombre_servicio": s["nombre_servicio"],
            "proveedor": s["proveedor"],
            "dia_pago": s["dia_pago"],
            "valor_mensual": s["valor_mensual"],
            "semaforo": semaforo,
            "color": color,
            "dias_restantes": dias_restantes
        })

    return render_template(
        "dashboard.html",
        total_prestamos=total_prestamos,
        total_servicios=total_servicios,
        cuotas_vencidas=cuotas_vencidas,
        cuotas_proximas=cuotas_proximas,
        cuotas_pendientes=cuotas_pendientes,
        cuotas_pagadas=cuotas_pagadas,
        total_general=total_prestamos + total_servicios,
        servicios_vencidos=servicios_vencidos,
        servicios_por_vencer=servicios_por_vencer,
        cantidad_servicios=len(servicios_db),
        servicios_lista=servicios_lista
    )


@app.route("/probar_correo")
def probar_correo():
    try:
        enviar_correo(
            "Prueba de correo - Control Angi Tatiana",
            """
            <h2>✅ Prueba exitosa</h2>
            <p>El sistema Control Angi Tatiana ya puede enviar correos desde Render usando API HTTPS.</p>
            """,
            "Prueba exitosa. El sistema ya puede enviar correos desde Render usando API HTTPS."
        )
        return "Correo de prueba enviado correctamente."
    except Exception as e:
        return f"Error enviando correo: {e}", 500


@app.route("/revisar_vencimientos")
def revisar_vencimientos():
    try:
        alertas_prestamos, alertas_servicios = obtener_alertas_vencimientos()

        if not alertas_prestamos and not alertas_servicios:
            return "No hay alertas para enviar."

        html, texto = construir_correo_alertas(alertas_prestamos, alertas_servicios)

        enviar_correo(
            "Alertas de vencimiento - Control Angi Tatiana",
            html,
            texto
        )

        return "Alertas enviadas correctamente."
    except Exception as e:
        return f"Error revisando vencimientos: {e}", 500


init_db()

if __name__ == "__main__":
    app.run(debug=True)