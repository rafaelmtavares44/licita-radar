import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { Barreira } from "./componentes/Barreira";
import "./estilo.css";

createRoot(document.getElementById("raiz")!).render(
  <StrictMode>
    {/* Tela branca não é erro: é ausência de erro visível. */}
    <Barreira>
      <App />
    </Barreira>
  </StrictMode>,
);
