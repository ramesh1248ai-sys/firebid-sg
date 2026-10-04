# Development identity provider. Production uses Microsoft Entra ID; this realm issues tokens
# in Entra's shape (oid, preferred_username, roles), so application code has no dev-only path.
FROM quay.io/keycloak/keycloak:26.4
COPY firebid-realm.json /opt/keycloak/data/import/firebid-realm.json
