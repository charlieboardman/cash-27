/* English / Español for the expense report page (recipient and administrator)
   and the employee sign-in. The choice is remembered in this browser. */
(function (global) {
  const spanish = {
    // Sign in
    "Sign in to your expense report": "Inicie sesión en su reporte de gastos",
    "Shared password": "Contraseña compartida",
    "Keep me signed in on this device": "Mantener la sesión iniciada en este dispositivo",
    "For up to 90 days between visits, or until you log out.": "Hasta 90 días entre visitas, o hasta que cierre la sesión.",
    "Continue": "Continuar",
    "Checking…": "Verificando…",
    "Unable to sign in": "No se pudo iniciar sesión",
    "Wrong password": "Contraseña incorrecta",
    "Shared password required": "Se requiere la contraseña compartida",

    // Report
    "Expense report": "Reporte de gastos",
    "Draft": "Borrador",
    "Submitted": "Enviado",
    "Approved": "Aprobado",
    "Closed": "Cerrado",
    "Void": "Anulado",
    "Corrections requested:": "Correcciones solicitadas:",
    "Cash received": "Efectivo recibido",
    "Left according to this report": "Restante según este reporte",
    "Everything is accounted for.": "Todo está justificado.",
    "You spent more pesos than you received and have dollars left. Your administrator will settle the exchange.":
      "Gastó más pesos de los que recibió y le quedan dólares. El administrador resolverá el cambio de moneda en el cierre.",
    "You spent more than you received.": "Gastó más de lo que recibió.",
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
    "Notes for this expense report": "Notas para este reporte de gastos",
    "Cash settlement": "Cierre de efectivo",
    "Missing cash (only if your totals don't add up)": "Efectivo faltante (solo si sus totales no cuadran)",
    "Missing cash": "Efectivo faltante",
    "Explanation": "Explicación",
    "I acknowledge the missing cash.": "Reconozco el efectivo faltante.",
    "Cash remaining to return": "Efectivo restante por devolver",
    "Personal funds used (reimbursement requested)": "Fondos personales utilizados (se solicita reembolso)",
    "Submit report": "Enviar reporte",
    "Submit corrections": "Enviar correcciones",
    "Reopen to edit": "Reabrir para editar",
    "Retry saving": "Reintentar guardar",
    "Row deleted": "Fila eliminada",
    "Receipt removed": "Recibo quitado",
    "Undo": "Deshacer",
    "Left": "Restante",
    "Show": "Mostrar",
    "Hide": "Ocultar",
    "Saving…": "Guardando…",
    "All changes saved": "Todos los cambios guardados",
    "Changes not saved": "Cambios sin guardar",
    "Cash activity": "Movimientos de efectivo",
    "No cash activity yet.": "Aún no hay movimientos de efectivo.",
    "Originally received": "Efectivo recibido inicialmente",
    "Additional cash received": "Efectivo adicional recibido",
    "Cash returned": "Efectivo devuelto",
    "Open full size": "Abrir en tamaño completo",
    "Close": "Cerrar",

    // Rows
    "New receipt": "Recibo nuevo",
    "Receipt": "Recibo",
    "No receipt": "Sin recibo",
    "Needs codes": "Faltan códigos",
    "Needs correction": "Requiere corrección",
    "Correction requested:": "Corrección solicitada:",
    "Split into {count} parts": "Dividido en {count} partes",
    "Choose…": "Elegir…",
    "View receipt": "Ver recibo",
    "Replace": "Reemplazar",
    "Remove": "Quitar",
    "Take photo": "Tomar foto",
    "Choose files": "Elegir archivos",
    "Add receipt": "Agregar recibo",
    "Needs statement": "Falta declaración",
    "Attach purchase log": "Adjuntar registro de compras",
    "View purchase log": "Ver registro de compras",
    "Print form for purchases without receipts": "Imprimir formato para compras sin recibo",
    "View statement": "Ver declaración",
    "Print statement": "Imprimir declaración",
    "Attach signed statement": "Adjuntar declaración firmada",
    "Print missing statements": "Imprimir declaraciones pendientes",
    "Attach": "Adjuntar",
    "No pages yet.": "Aún no hay páginas.",
    "{count} page": "{count} página",
    "{count} pages": "{count} páginas",
    "Page {page}": "Página {page}",
    "Document page {page}": "Página {page} del documento",
    "Move page {page} earlier": "Mover la página {page} antes",
    "Move page {page} later": "Mover la página {page} después",
    "Remove page {page}": "Quitar la página {page}",
    "Preparing page {page} of {total}…": "Preparando la página {page} de {total}…",
    "One document can contain up to {count} selected photos or files.":
      "Un documento puede tener hasta {count} fotos o archivos seleccionados.",
    "No receipt? Explain why": "¿Sin recibo? Explique por qué",
    "Date": "Fecha",
    "Vendor": "Proveedor",
    "Expense description": "Descripción del gasto",
    "Overall receipt description": "Descripción general del recibo",
    "Note": "Nota",
    "Use a negative amount for change received.": "Use un monto negativo para el cambio recibido.",
    "Receipt total ({currency})": "Total del recibo ({currency})",
    "Split receipt": "Dividir recibo",
    "Edit split": "Editar división",
    "Cancel": "Cancelar",
    "Done": "Listo",
    "No receipt attached": "No hay recibo adjunto",
    "Split amounts": "Montos divididos",
    "Receipt amounts": "Montos del recibo",
    "Receipt total": "Total del recibo",
    "Allocations": "Asignaciones",
    "Split details": "Detalles de la parte",
    "Split {number}": "Parte {number}",
    "Add another split": "Agregar otra parte",
    "Apply codes to all splits": "Aplicar códigos a todas las partes",
    "Still to assign:": "Falta asignar:",
    "Enter the receipt amount before finishing the split.": "Capture el monto del recibo antes de terminar la división.",
    "The split amounts must add up to the receipt.": "Las partes deben sumar el monto del recibo.",
    "The split receipt totals must add up to the receipt total.": "Los totales de las partes deben sumar el total del recibo.",
    "Still to assign: {amount}": "Falta asignar: {amount}",
    "The parts add up.": "Las partes suman el total.",
    "Remove part": "Quitar parte",
    "Undo split": "Deshacer división",
    "Delete row": "Eliminar fila",
    "Uploading receipt…": "Subiendo recibo…",
    "Use PDF, JPEG, or PNG files.": "Use archivos PDF, JPEG o PNG.",
    "Enter amounts with at most two decimal places.": "Capture montos con un máximo de dos decimales.",
    "The last save may have gone through. Reload the page before changing it.":
      "Es posible que el último guardado se haya registrado. Recargue la página antes de cambiarlo.",
    "This row changed on another screen. Your edits are unsaved; reload before editing again.":
      "Esta fila cambió en otra pantalla. Sus cambios no están guardados; recargue antes de volver a editar.",

    // Administrator tools on the same page
    "Project (optional)": "Proyecto (opcional)",
    "Request corrections": "Solicitar correcciones",
    "Approve": "Aprobar",
    "What needs to be corrected?": "¿Qué hay que corregir?",
    "Notes on individual rows are added in each row.": "Las notas sobre filas individuales se agregan en cada fila.",
    "Correction note for the recipient": "Nota de corrección para el destinatario",
    "Send back to recipient": "Devolver al destinatario",
    "Settlement date": "Fecha de cierre",
    "Approver name": "Nombre de quien aprueba",
    "USD returned": "USD devueltos",
    "MXN returned": "MXN devueltos",
    "USD reimbursed": "USD reembolsados",
    "MXN reimbursed": "MXN reembolsados",
    "Explanation (needed when amounts differ from the report or it has a note)":
      "Explicación (necesaria cuando los montos difieren del reporte o este tiene una nota)",
    "I have received the returned cash and paid any reimbursement shown above.":
      "He recibido el efectivo devuelto y pagado el reembolso indicado arriba.",
    "Paperwork": "Documentos",
    "Print voucher": "Imprimir vale",
    "Print": "Imprimir",
    "Needs: {list}": "Falta: {list}",
    "Nothing is needed.": "No falta nada.",
    "Additional cash": "Efectivo adicional",
    "File signed copy": "Archivar copia firmada",
    "Signed voucher not filed yet": "El vale firmado aún no está archivado",
    "File in Review": "Archivar en Revisión",
    "Add cash": "Agregar efectivo",
    "Cash return": "Devolución de efectivo",
    "Reason": "Motivo",
    "Save": "Guardar",
    "Save and print addendum": "Guardar e imprimir anexo",
    "Saved {id}.": "Se guardó {id}.",
    "Or choose from the inbox": "O elija de la bandeja de entrada",
    "Attach scanned {number}": "Adjuntar {number} escaneada",
    "Scanned purchase log: {name}": "Registro de compras escaneado: {name}",
    "Attach to {row}": "Adjuntar a {row}",
    "log row": "fila del registro",
    "Add as purchase log row": "Agregar como fila del registro de compras",
    "Document": "Documento",
    "Voucher": "Vale",
    "Addendum": "Anexo",
    "No-receipt statement": "Declaración sin comprobante",
    "Purchase log": "Registro de compras",
    "Count sheet": "Hoja de conteo",
    "Awaiting signed-paper review": "Esperando revisión del papel firmado",
    "Cash remaining": "Queda efectivo",
    "USD value unresolved": "Valor en USD sin resolver",
    "Needs signed voucher": "Falta el vale firmado",
    "Needs signed addendum": "Falta el anexo firmado",
    "Receipts in progress": "Recibos en proceso",
    "Receipts awaiting review": "Recibos pendientes de revisión",
    "Needs settlement": "Falta el cierre",
    "Open": "Abierto",
    "Complete": "Completo",
    "Replaced": "Reemplazado",

    // Server messages (a leading "Row N: " is translated separately)
    "enter the cash amount": "capture el monto de efectivo",
    "explain what happened to the cash": "explique qué pasó con el efectivo",
    "attach the signed statement": "adjunte la declaración firmada",
    "The totals do not add up. Enter the missing cash and explain it.":
      "Los totales no cuadran. Capture el efectivo faltante y explíquelo.",
    "Acknowledge the missing cash to approve": "Reconozca el efectivo faltante para aprobar",
    "attach the receipt or the signed no-receipt statement": "adjunte el recibo o la declaración firmada sin comprobante",
    "This report already has its purchase log": "Este reporte ya tiene su registro de compras",
    "enter the receipt date": "capture la fecha del recibo",
    "enter the amount spent": "capture el monto gastado",
    "describe what it was for": "describa para qué fue",
    "attach the receipt or explain why there is none": "adjunte el recibo o explique por qué no hay",
    "enter the receipt total in MXN": "capture el total del recibo en MXN",
    "enter the receipt total in USD": "capture el total del recibo en USD",
    "a split needs two or more parts": "una división necesita dos o más partes",
    "the split amounts must add up to the row": "las partes deben sumar el monto de la fila",
    "the split receipt totals must add up to the receipt total": "los totales de las partes deben sumar el total del recibo",
    "Add at least one row before submitting": "Agregue al menos una fila antes de enviar",
    "Your report was submitted. Reopen it to make changes.": "Su reporte fue enviado. Reábralo para hacer cambios.",
    "This signout is closed": "Esta entrega de efectivo está cerrada",
    "That receipt document is unavailable": "Ese documento de recibo no está disponible",
    "Enter a valid date": "Capture una fecha válida",
    "The receipt total cannot be negative": "El total del recibo no puede ser negativo",
    "Choose an available class code": "Elija un código de clase disponible",
    "Choose an available account code": "Elija un código de cuenta disponible",
    "This row is approved. Correct it from the ledger.": "Esta fila está aprobada y ya no se puede cambiar.",
    "This row is approved. Void it from the ledger.": "Esta fila está aprobada y ya no se puede eliminar.",
    "Your session expired. Reload the page and sign in again.": "Su sesión caducó. Vuelva a cargar la página e inicie sesión de nuevo.",
    "These records changed. Reload the page before trying again.": "Estos registros cambiaron. Vuelva a cargar la página antes de intentarlo de nuevo.",
    "Check the date and amounts. Use at most two decimal places.": "Revise la fecha y los montos. Use un máximo de dos decimales.",
    "Unable to complete the request. Please try again.": "No se pudo completar la solicitud. Intente de nuevo.",
  };

  let language = "en";
  try {
    language = localStorage.getItem("cash-language") ||
      localStorage.getItem("cash-signee-language") ||
      (navigator.language.startsWith("es") ? "es" : "en");
  } catch (_) { /* Storage can be unavailable in private browsing. */ }
  if (language !== "es") language = "en";

  // Messages shown in Spanish that have no translation yet (tests read this).
  const missing = new Set();

  function t(key, values = {}) {
    if (language === "es" && !spanish[key] && /[a-z]{3}/.test(key)) missing.add(key);
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
    document.querySelectorAll("[data-language]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.language === language));
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
    const button = event.target.closest("[data-language]");
    if (!button) return;
    language = button.dataset.language;
    try { localStorage.setItem("cash-language", language); } catch (_) { /* See above. */ }
    apply();
    document.dispatchEvent(new CustomEvent("language-change"));
  });

  global.cashI18n = { t, apply, error, missing, get language() { return language; } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
})(window);
