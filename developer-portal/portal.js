(function () {
  const params = new URLSearchParams(window.location.search);
  const apiBase = (params.get("apiBase") || "http://localhost:8000").replace(/\/$/, "");

  const setText = (id, text) => {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = text;
    }
  };

  const setHref = (id, href) => {
    const el = document.getElementById(id);
    if (el) {
      el.href = href;
    }
  };

  setText("api-base", `API Base: ${apiBase}`);
  setText("docs-url", `${apiBase}/docs`);
  setText("openapi-url", `${apiBase}/openapi.json`);
  setText("quickstart-url", `${apiBase}/developer/quickstart`);

  setHref("docs-link", `${apiBase}/docs`);
  setHref("sandbox-link", `${apiBase}/docs#/auth/sandbox_signup_auth_sandbox_signup_post`);

  setText(
    "sample-curl",
    `curl -X POST "${apiBase}/auth/sandbox/signup" \\\n+  -H "Content-Type: application/json" \\\n+  -d '{"username":"demo_dev"}'`
  );

  setText(
    "step1",
    `curl -X POST "${apiBase}/auth/sandbox/signup" \\\n+  -H "Content-Type: application/json" \\\n+  -d '{"username":"demo_dev"}'`
  );

  setText(
    "step2",
    `curl -X POST "${apiBase}/v1/documents" \\\n+  -H "Authorization: Bearer TU_TOKEN" \\\n+  -F "file=@./mi_csf.pdf" \\\n+  -F "document_type=csf"`
  );

  setText(
    "step3",
    `curl -X GET "${apiBase}/v1/documents/DOCUMENT_ID" \\\n+  -H "Authorization: Bearer TU_TOKEN"`
  );
})();
