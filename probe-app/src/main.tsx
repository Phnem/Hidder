import React from "react";
import ReactDOM from "react-dom/client";
import { StandaloneApp } from "./StandaloneApp";
import "./standalone.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("#root is missing from index.html");
}

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <StandaloneApp />
  </React.StrictMode>,
);
