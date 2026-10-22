import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { loadConfig } from "./api";
import App from "./App";
import "./styles.css";

loadConfig().then(() =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  ),
);
