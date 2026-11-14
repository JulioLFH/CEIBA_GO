# Ceiba GO

App de entregas de materiales **puerta a puerta** (dirección inicial → dirección final), según el documento
*Ceiba GO — flujo de punta a punta* (14 pasos · 7 fases · 5 actores). Django 5.2, SQLite, puerto **8040**.

## Iniciar

Doble clic en `iniciar.bat` (crea el entorno si falta, migra y levanta el servidor en `http://127.0.0.1:8040`,
accesible desde la red local por la IP de la PC).

Datos de demostración (una sola vez): `.venv\Scripts\python.exe manage.py seed_demo`

| Usuario | Rol | Contraseña |
|---|---|---|
| `operador` | Operaciones + admin (`/admin/`) | `ceiba2026` |
| `cliente` | Persona (boleta) | `ceiba2026` |
| `empresa` | Empresa con línea de crédito S/ 5,000 (factura) | `ceiba2026` |
| `agente_lima`, `agente_lima2`, `agente_arequipa`, `agente_cusco`, `agente_trujillo`, `agente_piura` | Agentes por zona | `ceiba2026` |
| `transporte`, `transporte2` | Empresas de transporte | `ceiba2026` |

Cambie estas contraseñas antes de usarlo con clientes reales.

## Flujo implementado

| Fase | Paso | Actor | Dónde |
|---|---|---|---|
| F0 Cuenta | 1 Crea su cuenta (persona/empresa RUC) | Cliente | `/registro/` |
| F1 Cotización | 2 Arma el envío (origen → destino, paquetes) · 3 Cotiza al instante | Cliente / Ceiba GO | `/app/nuevo/`, `POST /api/cotizar/` |
| F2 Pago + docs | 4 Paga (Yape/Plin/billetera/tarjeta) o crédito · 5 Boleta/factura + GRE-R · 6 Venta/CxC al ERP | Cliente / Ceiba GO / ERP | `/app/pedido/<código>/pagar/` |
| F3 Recojo | 7 Asigna el recojo (automático al agente de la zona, reasignable) · 8 Recoge escaneando QR, peso real y foto | Ceiba GO / Agente | `/ops/pedido/…`, `/agente/pedido/…` |
| F4 Interprovincial | 9 Entrega a transporte, emite GRE-T (manual / QR / API) · 10 QR en ruta | Ceiba GO / Transporte | `/ops/pedido/…`, `/transporte/` |
| F5 Destino | 11 Recepciona escaneando QR, almacena · 12 Entrega final con POD (firma, DNI, foto) | Agente | `/agente/pedido/…` |
| F6 Cierre | 13 Cierra el pedido · 14 Concilia venta, cobranza y almacén en el ERP | Ceiba GO / ERP | automático + `/ops/erp/` |

- **Regla transversal:** cada tramo valida el QR de su guía (GRE-R en el recojo, GRE-T en ruta y en la recepción) y que esté aceptada por SUNAT; si no, no avanza.
- **Estados:** Creado → Pagado/Facturado → Recojo asignado → Recogido → En tránsito → En destino → En reparto → Entregado. Excepciones: Fallido, Reprogramado, Devuelto y Anulado (con baja ante SUNAT y extorno o liberación del crédito).
- **Mismo departamento:** no hay tramo interprovincial; tras el recojo pasa directo a *En destino*.
- **Seguimiento sin cuenta:** `/s/<código>/` o `/seguimiento/`.
- **Multimoneda:** PEN o USD con tipo de cambio (Configuración).

## Decisiones abiertas del documento (cómo quedaron)

1. **Tarifas:** base por ruta + S/ por kg facturable; se cobra por el **mayor** entre el peso real y el volumétrico (m³ × 250). Las rutas sin tarifa propia usan reglas por región. Todo se edita en `/admin/` (Tarifas por ruta, Configuración).
2. **Emisión electrónica:** `envios/sunat.py` es el punto de integración. Hoy está en modo *simulado* (numera y acepta al instante). Para producción hay que conectar Ceiba ERP o un PSE/OSE.
3. **Transportistas:** cualquiera registrado en el admin. Los que tienen `tiene_api` aceptan el registro por API; los demás, manual o QR.
4. **Seguro:** 1 % del valor declarado (configurable). Falta definir quién responde por pérdida o daño en cada tramo.

## Ceiba ERP

Cada hecho se encola en `SyncERP` (venta, GRE, CxC, almacén, centro de costo, cobranza, baja y cierre). Sin URL configurada,
Ceiba GO trabaja **independiente** y sincroniza al reconectar: botón *Sincronizar ahora* en `/ops/erp/` o
`manage.py sincronizar_erp`. Envía `POST {URL_ERP}/api/ceibago/<tipo>/` con `Authorization: Token …`.
Ese endpoint todavía **no existe en Ceiba ERP**: falta crearlo allí.

## Pruebas

`.venv\Scripts\python.exe manage.py test envios`: recorre el flujo completo por HTTP (incluye el QR equivocado,
el fallido y reprogramado, el mismo departamento, la anulación y el crédito solo para empresas).
