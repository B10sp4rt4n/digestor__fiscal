export type SandboxSignupResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  username: string;
  role: string;
  tenant_id: string;
  docs_url: string;
  openapi_url: string;
};

export class DigestorClient {
  private token?: string;

  constructor(private baseUrl: string) {}

  setToken(token: string): void {
    this.token = token;
  }

  private headers(): HeadersInit {
    const headers: Record<string, string> = {
      "Accept": "application/json",
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }
    return headers;
  }

  async sandboxSignup(username?: string, password?: string): Promise<SandboxSignupResponse> {
    const response = await fetch(`${this.baseUrl}/auth/sandbox/signup`, {
      method: "POST",
      headers: {
        ...this.headers(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username, password }),
    });

    if (!response.ok) {
      throw new Error(`Sandbox signup failed: ${response.status} ${await response.text()}`);
    }

    const data = (await response.json()) as SandboxSignupResponse;
    this.token = data.access_token;
    return data;
  }

  async login(username: string, password: string): Promise<{ access_token: string }> {
    const response = await fetch(`${this.baseUrl}/auth/login`, {
      method: "POST",
      headers: {
        ...this.headers(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username, password }),
    });

    if (!response.ok) {
      throw new Error(`Login failed: ${response.status} ${await response.text()}`);
    }

    const data = (await response.json()) as { access_token: string };
    this.token = data.access_token;
    return data;
  }

  async uploadDocument(file: Blob, filename = "document.pdf", documentType = "csf"): Promise<any> {
    const formData = new FormData();
    formData.append("file", file, filename);
    formData.append("document_type", documentType);

    const response = await fetch(`${this.baseUrl}/v1/documents`, {
      method: "POST",
      headers: this.headers(),
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`Upload failed: ${response.status} ${await response.text()}`);
    }

    return response.json();
  }

  async getDocument(documentId: string): Promise<any> {
    const response = await fetch(`${this.baseUrl}/v1/documents/${documentId}`, {
      method: "GET",
      headers: this.headers(),
    });

    if (!response.ok) {
      throw new Error(`Get document failed: ${response.status} ${await response.text()}`);
    }

    return response.json();
  }
}
