/**
 * Buscador de Aldea Pucela para las webs del ecosistema.
 *
 * Se pega en cualquiera de las webs estáticas con:
 *   <script src="https://buscador.aldeapucela.org/buscador.js" defer></script>
 * y se abre con la tecla "/" o con cualquier elemento que tenga [data-buscador].
 *
 * Solo pide scope público: el chat de Telegram nunca sale por aquí (además, el filtro de
 * verdad está en el SQL del servidor, esto es solo el cliente).
 */
(() => {
  const API = document.currentScript?.dataset.api || "https://buscador.aldeapucela.org";
  const ESPERA_MS = 250;

  const FUENTES = {
    otrapucela: "La Otra Pucela",
    forum: "Foro",
    evento: "Agenda",
  };

  const css = `
  .ap-buscador{position:fixed;inset:0;z-index:9999;display:none;background:rgba(0,0,0,.5);
    padding:8vh 1rem 1rem;overflow-y:auto}
  .ap-buscador[open]{display:block}
  .ap-caja{max-width:44rem;margin:0 auto;background:#fff;border-radius:.75rem;overflow:hidden;
    box-shadow:0 10px 40px rgba(0,0,0,.3);font-family:system-ui,-apple-system,sans-serif}
  .ap-caja input{width:100%;border:0;border-bottom:1px solid #e5e7eb;padding:1rem 1.1rem;
    font-size:1.05rem;outline:0;box-sizing:border-box;color:inherit;background:transparent}
  .ap-lista{list-style:none;margin:0;padding:0;max-height:60vh;overflow-y:auto}
  .ap-lista li{border-bottom:1px solid #f3f4f6}
  .ap-lista a{display:block;padding:.8rem 1.1rem;text-decoration:none;color:inherit}
  .ap-lista a:hover,.ap-lista a:focus{background:#f9fafb;outline:0}
  .ap-titulo{font-weight:600;margin-bottom:.2rem;line-height:1.3}
  .ap-meta{font-size:.78rem;color:#6b7280;display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:.25rem}
  .ap-etiqueta{background:#eef2ff;color:#4338ca;border-radius:.25rem;padding:.05rem .4rem}
  .ap-caducado{background:#fef3c7;color:#92400e;border-radius:.25rem;padding:.05rem .4rem}
  .ap-frag{font-size:.86rem;color:#4b5563;line-height:1.45}
  .ap-nota{padding:1rem 1.1rem;color:#6b7280;font-size:.9rem}
  @media (prefers-color-scheme:dark){
    .ap-caja{background:#111827;color:#f9fafb}
    .ap-caja input{border-bottom-color:#374151}
    .ap-lista li{border-bottom-color:#1f2937}
    .ap-lista a:hover,.ap-lista a:focus{background:#1f2937}
    .ap-frag{color:#d1d5db}
  }`;

  const raiz = document.createElement("div");
  raiz.className = "ap-buscador";
  raiz.innerHTML = `<style>${css}</style>
    <div class="ap-caja" role="dialog" aria-modal="true" aria-label="Buscar en Aldea Pucela">
      <input type="search" placeholder="Buscar en el foro, La Otra Pucela y la agenda…"
             aria-label="Buscar" autocomplete="off">
      <ul class="ap-lista" role="listbox"></ul>
      <p class="ap-nota"></p>
    </div>`;
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(raiz));

  const campo = raiz.querySelector("input");
  const lista = raiz.querySelector(".ap-lista");
  const nota = raiz.querySelector(".ap-nota");

  const abrir = () => {
    raiz.setAttribute("open", "");
    campo.focus();
    campo.select();
  };
  const cerrar = () => raiz.removeAttribute("open");

  const escapar = (t) =>
    String(t ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const fecha = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric" });
  };

  function pintar(datos) {
    lista.innerHTML = datos.results
      .map(
        (r) => `<li><a href="${escapar(r.url)}">
          <div class="ap-meta">
            <span class="ap-etiqueta">${escapar(FUENTES[r.source_type] || r.source_type)}</span>
            ${r.date ? `<span>${escapar(fecha(r.date))}</span>` : ""}
            ${r.expired ? '<span class="ap-caducado">ya pasó</span>' : ""}
          </div>
          <div class="ap-titulo">${escapar(r.title)}</div>
          <div class="ap-frag">${escapar(r.snippet)}</div>
        </a></li>`
      )
      .join("");
    nota.textContent = datos.results.length
      ? ""
      : "No hay nada sobre eso. Prueba con otras palabras.";
  }

  let temporizador;
  let ultima = 0;
  campo.addEventListener("input", () => {
    clearTimeout(temporizador);
    const q = campo.value.trim();
    if (q.length < 3) {
      lista.innerHTML = "";
      nota.textContent = "";
      return;
    }
    temporizador = setTimeout(async () => {
      const sello = ++ultima;
      nota.textContent = "Buscando…";
      try {
        const r = await fetch(`${API}/search?q=${encodeURIComponent(q)}&limit=8`);
        if (!r.ok) throw new Error(r.status);
        const datos = await r.json();
        // Descarta respuestas de consultas que el usuario ya ha dejado atrás.
        if (sello === ultima) pintar(datos);
      } catch (e) {
        if (sello === ultima) {
          lista.innerHTML = "";
          nota.textContent = "El buscador no responde ahora mismo.";
        }
      }
    }, ESPERA_MS);
  });

  document.addEventListener("keydown", (e) => {
    const escribiendo = /^(input|textarea|select)$/i.test(e.target.tagName) ||
      e.target.isContentEditable;
    if (e.key === "/" && !escribiendo && !raiz.hasAttribute("open")) {
      e.preventDefault();
      abrir();
    } else if (e.key === "Escape" && raiz.hasAttribute("open")) {
      cerrar();
    }
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-buscador]")) {
      e.preventDefault();
      abrir();
    } else if (e.target === raiz) {
      cerrar();
    }
  });
})();
