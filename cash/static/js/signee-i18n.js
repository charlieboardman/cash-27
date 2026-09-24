/* Spanish for the recipient's expense report and sign-in. The language choice
   is remembered in this browser. Administrators see English. */
(function (global) {
  const spanish = {
    // Sign in
    "Sign in to your expense report": "Inicie sesión en su informe de gastos",
    "Shared password": "Contraseña compartida",
    "Keep me signed in on this device": "Mantener la sesión iniciada en este dispositivo",
    "For up to 90 days between visits, or until you log out.": "Hasta 90 días entre visitas, o hasta que cierre la sesión.",
    "Continue": "Continuar",
    "Checking…": "Verificando…",
    "Unable to sign in": "No se pudo iniciar sesión",
    "Wrong password": "Contraseña incorrecta",
    "Shared password required": "Se requiere la contraseña compartida",

    // Report
    "Expense report": "Informe de gastos",
    "Draft": "Borrador",
    "Submitted": "Enviado",
    "Approved": "Aprobado",
    "Closed": "Cerrado",
    "Void": "Anulado",
    "Corrections requested:": "Correcciones solicitadas:",
    "Cash received": "Efectivo recibido",
    "Left according to this report": "Restante según este informe",
    "Everything is accounted for.": "Todo está justificado.",
    "You spent more pesos than you received and have dollars left. Your administrator will settle the exchange.":
      "Gastó más pesos de los que recibió y le quedan dólares. El administrador liquidará el cambio.",
    "Cash left over goes back to the safe.": "El efectivo sobrante se devuelve a la caja fuerte.",
    "Add cash return": "Agregar devolución de efectivo",
    "You spent more than you received.": "Gastó más de lo que recibió.",
    "Request reimbursement": "Solicitar reembolso",
    "Explain the difference below.": "Explique la diferencia abajo.",
    "Rows": "Filas",
    "Select all": "Seleccionar todo",
    "Select row": "Seleccionar fila",
    "{count} row selected": "{count} fila seleccionada",
    "{count} rows selected": "{count} filas seleccionadas",
    "Class code": "Código de clase",
    "Account code": "Código de cuenta",
    "Leave unchanged": "Sin cambios",
    "Apply to selected": "Aplicar a las seleccionadas",
    "Choose a class code or an account code to apply.": "Elija un código de clase o de cuenta para aplicar.",
    "Codes applied.": "Códigos aplicados.",
    "Add a row for each receipt.": "Agregue una fila por cada recibo.",
    "Add row": "Agregar fila",
    "Anything to explain?": "¿Algo que explicar?",
    "Submit report": "Enviar informe",
    "Submit corrections": "Enviar correcciones",
    "Reopen to edit": "Reabrir para editar",
    "Retry saving": "Reintentar guardar",
    "Saving…": "Guardando…",
    "All changes saved": "Todos los cambios guardados",
    "Changes not saved": "Cambios sin guardar",
    "Cash activity": "Movimientos de efectivo",
    "No cash activity yet.": "Aún no hay movimientos de efectivo.",
    "Originally received": "Efectivo recibido inicialmente",
    "Additional cash received": "Efectivo adicional recibido",
    "Cash returned": "Efectivo devuelto",
    "Cash balance corrected": "Saldo de efectivo corregido",
    "Open full size": "Abrir en tamaño completo",
    "Close": "Cerrar",

    // Rows
    "New receipt": "Recibo nuevo",
    "Reimbursement requested": "Reembolso solicitado",
    "Receipt": "Recibo",
    "Row type": "Tipo de fila",
    "No receipt": "Sin recibo",
    "Needs codes": "Faltan códigos",
    "Needs correction": "Requiere corrección",
    "Correction requested:": "Corrección solicitada:",
    "Split in {count}": "Dividido en {count}",
    "Choose…": "Elegir…",
    "View receipt": "Ver recibo",
    "Add page": "Agregar página",
    "Replace": "Reemplazar",
    "Remove": "Quitar",
    "Take photo": "Tomar foto",
    "Choose file": "Elegir archivo",
    "No receipt? Explain why": "¿Sin recibo? Explique por qué",
    "Date": "Fecha",
    "Vendor": "Proveedor",
    "What was it for?": "¿Para qué fue?",
    "Note": "Nota",
    "Use a negative amount for change received.": "Use un importe negativo para el cambio recibido.",
    "Receipt total ({currency})": "Total del recibo ({currency})",
    "Split receipt": "Dividir recibo",
    "Still to assign: {amount}": "Falta asignar: {amount}",
    "The parts add up.": "Las partes suman el total.",
    "Add part": "Agregar parte",
    "Remove part": "Quitar parte",
    "Undo split": "Deshacer división",
    "Delete row": "Eliminar fila",
    "Delete this row?": "¿Eliminar esta fila?",
    "Remove this receipt from the row?": "¿Quitar este recibo de la fila?",
    "Uploading receipt…": "Subiendo recibo…",
    "Unable to open receipt": "No se pudo abrir el recibo",
    "Use PDF, JPEG, or PNG files.": "Use archivos PDF, JPEG o PNG.",
    "Enter amounts with at most two decimal places.": "Ingrese importes con un máximo de dos decimales.",
    "This row changed on another screen. Your edits are unsaved; reload before editing again.":
      "Esta fila cambió en otra pantalla. Sus cambios no están guardados; recargue antes de volver a editar.",

    // Server messages (a leading "Row N: " is translated separately)
    "enter the cash amount": "ingrese el importe de efectivo",
    "enter the receipt date": "ingrese la fecha del recibo",
    "enter the amount spent": "ingrese el importe gastado",
    "describe what it was for": "describa para qué fue",
    "attach the receipt or explain why there is none": "adjunte el recibo o explique por qué no hay",
    "enter the receipt total in MXN": "ingrese el total del recibo en MXN",
    "enter the receipt total in USD": "ingrese el total del recibo en USD",
    "a split needs two or more parts": "una división necesita dos o más partes",
    "the split amounts must add up to the row": "las partes deben sumar el importe de la fila",
    "the split receipt totals must add up to the receipt total": "los totales de las partes deben sumar el total del recibo",
    "Add at least one row before submitting": "Agregue al menos una fila antes de enviar",
    "Your report was submitted. Reopen it to make changes.": "Su informe fue enviado. Reábralo para hacer cambios.",
    "This signout is closed": "Esta entrega está cerrada",
    "That receipt document is unavailable": "Ese documento de recibo no está disponible",
    "Enter a valid date": "Ingrese una fecha válida",
    "The receipt total cannot be negative": "El total del recibo no puede ser negativo",
    "Choose an available class code": "Elija un código de clase disponible",
    "Choose an available account code": "Elija un código de cuenta disponible",
    "This row is approved. Correct it from the ledger.": "Esta fila está aprobada y ya no se puede cambiar.",
    "This row is approved. Void it from the ledger.": "Esta fila está aprobada y ya no se puede eliminar.",
    "Your session expired. Reload the page and sign in again.": "Su sesión caducó. Vuelva a cargar la página e inicie sesión de nuevo.",
    "These records changed. Reload the page before trying again.": "Estos registros cambiaron. Vuelva a cargar la página antes de intentarlo de nuevo.",
    "Check the date and amounts. Use at most two decimal places.": "Revise la fecha y los importes. Use un máximo de dos decimales.",
    "Unable to complete the request. Please try again.": "No se pudo completar la solicitud. Inténtelo de nuevo.",
  };

  let language = "en";
  try {
    language = localStorage.getItem("cash-signee-language") ||
      (navigator.language.startsWith("es") ? "es" : "en");
  } catch (_) { /* Storage can be unavailable in private browsing. */ }
  if (language !== "es") language = "en";

  function t(key, values = {}) {
    const text = language === "es" ? (spanish[key] || key) : key;
    return text.replace(/\{(\w+)\}/g, (match, name) => values[name] ?? match);
  }

  function apply() {
    document.documentElement.lang = language;
    document.querySelectorAll("[data-i18n]").forEach(element => {
      element.textContent = t(element.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-aria-label]").forEach(element => {
      element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
    });
    document.querySelectorAll("[data-signee-language]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.signeeLanguage === language));
    });
  }

  function error(problem) {
    const message = problem?.message || "Request failed";
    if (language === "en") return message;
    const row = message.match(/^Row (\d+): (.*)$/);
    if (row && spanish[row[2]]) return `Fila ${row[1]}: ${spanish[row[2]]}`;
    if (spanish[message]) return spanish[message];
    const fallback = {
      csrf: "Your session expired. Reload the page and sign in again.",
      unauthorized: "Shared password required",
      conflict: "These records changed. Reload the page before trying again.",
      bad_request: "Check the date and amounts. Use at most two decimal places.",
    };
    return t(fallback[problem?.code] || "Unable to complete the request. Please try again.");
  }

  document.addEventListener("click", event => {
    const button = event.target.closest("[data-signee-language]");
    if (!button) return;
    language = button.dataset.signeeLanguage;
    try { localStorage.setItem("cash-signee-language", language); } catch (_) { /* See above. */ }
    apply();
    document.dispatchEvent(new CustomEvent("signee-language-change"));
  });

  global.signeeI18n = { t, apply, error, get language() { return language; } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
})(window);
