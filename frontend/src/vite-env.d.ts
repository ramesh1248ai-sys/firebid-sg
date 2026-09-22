/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The identity provider's issuer URL: Keycloak in development, Entra ID in production. */
  readonly VITE_OIDC_AUTHORITY?: string;
  readonly VITE_OIDC_CLIENT_ID?: string;
  readonly VITE_OIDC_SCOPE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
