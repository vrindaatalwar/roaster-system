import React from "react";
import ReactDOM from "react-dom/client";
import { ClerkProvider } from "@clerk/react";
import App from "./App";
import "./styles.css";

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {publishableKey
      ? <ClerkProvider publishableKey={publishableKey}><App /></ClerkProvider>
      : <main style={{ padding: 32, fontFamily: "sans-serif" }}>
          <h1>Clerk setup needed</h1>
          <p>Add the Clerk publishable key to <code>VITE_CLERK_PUBLISHABLE_KEY</code>, then restart the app.</p>
        </main>}
  </React.StrictMode>,
);
