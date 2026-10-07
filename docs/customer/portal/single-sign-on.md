# Single sign-on

Each organization configures its own SSO in the UI — no instance-wide identity
configuration. OpenID Connect and LDAP/Active Directory SSO are supported.

## Identity provider side

Any OpenID Connect provider works — Keycloak, Okta, Google, Microsoft Entra
ID, or an internal provider on a network with no internet access. Register
{{BRAND}} as a client (an "app registration") there and collect:

- The **issuer URL** — the base the provider serves
  `/.well-known/openid-configuration` from, e.g.
  `https://keycloak.internal/realms/acme`
- The client ID
- A client secret
- The redirect URI shown on the portal's SSO settings page

Grant the `openid`, `profile` and `email` scopes. For group mapping, have the
provider emit a claim listing the user's groups (`groups` at most providers;
`roles` or a namespaced claim at some) and grant whatever consent your
provider needs to read group memberships.

## Portal side

In **Organization settings → Single sign-on**, enter those values (the secret
is stored encrypted) and set:

- **Issuer URL** — your provider's OpenID Connect issuer (Microsoft Entra:
  `https://login.microsoftonline.com/<tenant-id>/v2.0`, Google:
  `https://accounts.google.com`, Okta: `https://<your-domain>.okta.com`,
  Keycloak: `https://<host>/realms/<realm>`). On save the portal fetches
  the issuer's `/.well-known/openid-configuration` and shows the
  authorization and token endpoints it found; a wrong issuer is refused
  with the discovery error, so it fails here rather than at someone's
  first login
- **Email domains** — which addresses route to this organization's SSO
- **Auto-provision** — create an account automatically on first successful
  login, or leave it off so SSO only works for people who are already
  members (an unrecognized login is refused with instructions to request an
  invitation)
- **Default role and groups** — what an auto-provisioned user gets
- **Groups claim** — the claim the provider lists the user's groups in
  (`groups` unless you change it)
- **Group mapping** — identity provider groups, as they appear in that
  claim, to {{BRAND}} permission groups
- **Extra scopes** — empty unless the provider only emits its groups claim
  for a scope of its own; the provider setup below says when
- **`allow_password_login`** — whether passwords still work alongside SSO
- **`enforce_sso`** — require SSO for this organization

Group mapping is re-applied on every login, so revoking a group in your
directory revokes the corresponding access at next sign-in.

An internal provider whose certificate comes from a private CA works without
disabling verification: point `REQUESTS_CA_BUNDLE` at your CA bundle in the
portal's environment. Discovery and the login flow share one HTTP client, so
that single variable covers both.

## Provider setup

The portal requests the `openid`, `profile` and `email` scopes and reads the
ID token and the userinfo response together, so a claim in either place
counts. What differs per provider is the issuer URL, where the email comes
from, and what it takes to get a groups claim into the token. **Extra
scopes** stays empty unless a subsection below says otherwise.

### Microsoft Entra ID

- Issuer: `https://login.microsoftonline.com/<directory-tenant-id>/v2.0`.
  Use the tenant ID, not `common` — discovery must name your tenant.
- **App registrations → New registration**, single tenant. Under
  **Authentication** add a **Web** platform with the portal's redirect URI;
  under **Certificates & secrets** create a client secret (note its
  expiry). The client ID is the **Application (client) ID** on the
  overview page.
- Email: `email` is present for accounts that have one. Accounts without a
  mailbox carry only `preferred_username` (the UPN), which the portal uses
  instead — so list the UPN domain under **Email domains** too.
- Groups: **Token configuration → Add groups claim**, pick **Security
  groups** (or **Groups assigned to the application** in a large tenant),
  ID token format **Group ID**. Values are group object IDs, so the mapping
  reads `<object-id> = <permission group>`. Choosing a name format there
  (**sAMAccountName**, or **Cloud-only group display names**, which needs
  the assigned-groups option) lets you map names instead.
- Above 200 groups the token carries an overage pointer instead of the
  list; the portal leaves that user's mapped groups untouched on such
  logins. Keep the count down with **Groups assigned to the application**.

### Google Workspace

- Issuer: `https://accounts.google.com`.
- **Google Cloud console → APIs & Services → Credentials → Create
  credentials → OAuth client ID**, type **Web application**, with the
  portal's redirect URI under **Authorized redirect URIs**. Set the consent
  screen's audience to **Internal** so only accounts in your Workspace can
  sign in.
- Email: `email` and `email_verified` always come with the `email` scope;
  `hd` names the Workspace domain.
- Groups: none. Google's OpenID Connect tokens carry no group membership,
  so leave **Group mapping** empty — an empty mapping touches nothing —
  and use **Default groups** or the portal's own group management instead.

### Okta

- Issuer: the org authorization server, `https://<your-okta-domain>`
  (e.g. `https://acme.okta.com`), or a custom authorization server such
  as `https://<your-okta-domain>/oauth2/default`.
- **Applications → Create App Integration → OIDC - OpenID Connect → Web
  Application**, with the portal's redirect URI under **Sign-in redirect
  URIs**. Assign the users or groups that may sign in; copy the client ID
  and secret.
- Email: `email` (and `preferred_username`, the Okta login) come with the
  default scopes.
- Groups on the org authorization server: open the app's **Sign On** tab,
  **OpenID Connect ID Token → Edit**, **Groups claim type: Filter**, claim
  name `groups`, **Matches regex** `.*` (or **Starts with** a prefix). The
  org server only emits the claim when the `groups` scope is requested, so
  enter `groups` under **Extra scopes**. On a custom authorization server
  add the claim under **Security → API → your server → Claims** (**Include
  in token type: ID Token**, **Value type: Groups**, any scope) and no
  extra scope is needed. Values are group names, e.g. `Analysts`; every
  user is also in `Everyone`.

### Ping Identity (PingOne)

- Issuer: `https://auth.pingone.com/<environment-id>/as` —
  `auth.pingone.eu`, `.ca`, `.asia` or `.com.au` for tenants in those
  regions.
- **Applications → + → OIDC Web App**. On **Configuration** choose the
  **Authorization Code** grant, add the portal's redirect URI and set token
  endpoint authentication to **Client Secret Basic**; grant the `openid`,
  `profile` and `email` scopes under **Resources**; enable the app.
- Email: `email` comes with the `email` scope from the user's email
  attribute.
- Groups: **Attribute Mappings → + Add**, claim `groups`, PingOne attribute
  **Group Names** (`memberOfGroupNames`; **Group IDs** to map by ID). A
  mapped claim is returned in the ID token regardless of scopes. Values
  are PingOne group names.

### OneLogin

- Issuer: `https://<subdomain>.onelogin.com/oidc/2`.
- **Applications → Add App → OpenId Connect (OIDC)**. On **Configuration**
  add the portal's redirect URI; on **SSO** choose application type **Web**
  and token endpoint authentication **Basic**; copy the client ID and
  secret. Give users access through a role.
- Email: `email` comes with the `email` scope.
- Groups: on **Parameters** open **Groups**, set **Default if no value** to
  **User Roles** with **Semicolon Delimited input (Multi-value output)**.
  OneLogin emits the claim only for the `groups` scope, so enter `groups`
  under **Extra scopes**. Values are role names. A user with no roles gets
  no claim at all, which the portal treats as membership of none.

### JumpCloud

- Issuer: `https://oauth.id.jumpcloud.com` (the app's SSO tab shows the
  issuer for your tenant).
- **SSO Applications → + Add New Application → Custom Application → Manage
  Single Sign-On → Configure SSO with OIDC**, with the portal's redirect
  URI under **Redirect URIs**, **Client Authentication Type: Client Secret
  Post**, standard scopes **Email** and **Profile**. Save, then copy the
  client ID and the secret, which is shown once. Connect the user groups
  that should have access on **User Groups**.
- Email: `email` comes with the Email scope.
- Groups: under **Attribute Mapping → Group Attributes** tick **include
  group attribute** and name it `groups`. Only the groups connected to the
  application are listed, by name.

### Auth0

- Issuer: `https://<tenant>.<region>.auth0.com` (e.g.
  `https://acme.eu.auth0.com`), or your custom domain.
- **Applications → Create Application → Regular Web Applications**, with
  the portal's redirect URI under **Allowed Callback URLs**. Enable the
  connections your users sign in with; copy the client ID and secret.
- Email: `email` and `email_verified` come with the `email` scope.
- Groups: Auth0 refuses custom claims named `groups` or `roles`; a custom
  claim must be namespaced with a URL you control. Add a **Login / Post
  Login** Action:

  ```js
  exports.onExecutePostLogin = async (event, api) => {
    api.idToken.setCustomClaim("https://example.com/groups", event.authorization?.roles ?? []);
  };
  ```

  then set **Groups claim** to `https://example.com/groups`. Values are the
  role names assigned under **User Management → Roles**.

### Keycloak

- Issuer: `https://<host>/realms/<realm>` (`/auth/realms/<realm>` on
  older versions).
- **Clients → Create client**, type OpenID Connect, **Client
  authentication** on, **Standard flow** on, with the portal's redirect URI
  under **Valid redirect URIs**. The secret is on the **Credentials** tab.
- Email: `email` comes with the `email` scope from the user's email field.
- Groups: on the client's **Client scopes** tab open the
  `<client>-dedicated` scope, **Add mapper → By configuration → Group
  Membership**, token claim name `groups`, **Full group path** off, **Add
  to ID token** on. Values are then plain group names; with the full path
  on they read `/parent/child` and the mapping must match. A **User Realm
  Role** mapper works the same way with **Groups claim** set to its claim
  name.

### AD FS (Windows Server 2016 and later)

- Issuer: `https://<federation-service-host>/adfs`.
- **AD FS Management → Application Groups → Add Application Group →
  Server application accessing a Web API**. Server application: note the
  client identifier, add the portal's redirect URI, **Generate a shared
  secret** and copy it. Web API: set the **Identifier** to that same client
  identifier. Under **Permitted scopes** tick `openid`, `profile`, `email`
  and `allatclaims`.
- On the Web API's **Issuance Transform Rules** add **Send LDAP Attributes
  as Claims**: **E-Mail-Addresses → E-Mail Address**, and **Token-Groups -
  Unqualified Names** with the outgoing claim type typed as `groups`.
- Enter `allatclaims` under **Extra scopes**: AD FS copies the rule-issued
  claims into the ID token only for that scope, and its userinfo endpoint
  returns nothing but `sub`.
- Email: `email` from the rule when the account has a mail attribute;
  `upn` is always present and is the fallback.
- Groups: `groups`, as Active Directory group names. A user in one group
  gets a plain string instead of a list, which the portal accepts. AD FS
  2016 needs update KB4019472 for `allatclaims`.

## LDAP / Active Directory

Directory sign-in is for on-premises directories with no federation layer in
front of them. If you have Microsoft Entra ID, AD FS (2016 or later), or any
cloud identity provider, use OpenID Connect instead: with OpenID Connect the
portal never handles the password.

For an organization whose identity lives in an on-premises directory — Active
Directory, OpenLDAP, FreeIPA — with no OpenID Connect provider in front of
it, or on a network with no route to one. Set **Identity source** to
*LDAP / Active Directory* on the same settings page. An organization has one
identity source: OpenID Connect or a directory, not both.

This works differently from OpenID Connect, and it is worth being plain about
it: **the password is typed into the portal's login form and the portal
checks it against your directory.**

Fields:

- **Server URI** — `ldaps://dc.corp.example:636`, or
  `ldap://dc.corp.example:389`, which the portal upgrades with StartTLS
  before anything else is sent. Every connection is TLS; there is no way to
  send a password in the clear. Referrals are never followed.
- **CA certificate** — the PEM certificate of the CA that signed your
  directory's certificate. Internal directories almost always use a private
  CA; paste it here. Certificate verification is always on — there is no
  option to disable it — and the server name is checked against the
  certificate.
- **Bind DN** and **bind password** — a read-only service account the portal
  searches with (the password is stored encrypted). It needs read access to
  the search base and to the users' entries; a directory that hides them
  from it answers "no such object", and the connection test says so. Leave
  the DN blank to search anonymously, if your directory allows it.
- **User search base** and **user filter** — where to look and how.
  `{login}` in the filter is replaced by the email typed at the login form
  (escaped). The default `(|(mail={login})(userPrincipalName={login}))`
  suits Active Directory and works unchanged on OpenLDAP, which ignores the
  `userPrincipalName` half it does not know. Exactly one entry must match.
- **Email, display name and groups attributes** — `mail`, `displayName`
  and `memberOf` by default. Active Directory always has `memberOf`; on
  OpenLDAP it exists only with the `memberof` overlay loaded, and only for
  groups added or changed after it was enabled. Without it the attribute is
  empty and no group mapping applies.

A login is a bind-then-search: the portal binds as the service account,
finds the user's entry, then binds again *as that entry* with the typed
password. That second bind is the credential check. The entry's email
attribute names the portal account (the typed email if the entry has none),
and it must sit inside the organization's email domains, as with OpenID
Connect. Membership rules are the same too: existing users must already be
members, new accounts are created only with auto-provisioning on.

**Group mapping** keys on the group's DN as it appears in the groups
attribute — `CN=Analysts,OU=Groups,DC=corp,DC=example` — and is re-applied
on every login like the OpenID Connect mapping.

**Email domains** route logins to the directory exactly as they route to an
OpenID Connect provider, and domain ownership (below) applies the same way.
With **`allow_password_login`** on, a password the directory rejects is
still tried against the portal's own account; with it off, or with
**`enforce_sso`** on, the directory is the only check. Members with a
portal MFA device are asked for their code after the directory accepts the
password, and an organization MFA policy applies to directory logins like
any other password login.

**Test directory connection** on the settings page binds with the saved
settings and reads the search base, so an unreachable server, a rejected
service account or a wrong base is diagnosed there rather than at someone's
failed login. A directory that cannot be reached during a login shows a
generic "sign-in is unavailable" message and records the error in the audit
trail. Passwords are never logged or recorded.

## Security model

Instance operators are exempt from SSO enforcement, so a misconfiguration
cannot lock everyone out of the instance. Linking an identity to a portal
account happens only for someone already a member of that org — a domain
being routed to SSO does not by itself grant access to it.

!!! warning
    With verification switched off, email-domain routing asserts domain
    ownership rather than verifying it. On an instance shared by organizations
    that do not trust each other, confirm domain claims out of band before
    enabling a new organization's SSO — or turn verification back on, below.

## Domain ownership

Whoever routes an email domain decides who may sign in as its users, so on a
shared instance a claim alone isn't enough: without proof, any org admin
could claim a domain they don't own and capture logins meant for someone
else.

**Organization settings → Single sign-on** has a domain ownership section.
Claim a domain, publish the TXT record it shows you, then verify:

```
_trellum-verification.example.com   TXT   trellum-verification=<token>
```

The token is per claim, so publishing someone else's proves nothing, and a
domain can be held by only one organization at a time.

Verification is enabled by default. A trusted single-organization development
instance may opt out temporarily, but shared or production installations
should keep it enabled. Once on, only verified domains route, both when
choosing where to send a login and when accepting the identity the identity
provider asserts on the way back.

### Turning verification back on

An instance that opted out and later needs verification — a second
organization is joining, say — must not simply switch it on. Once the
setting is on, only verified domains route. An organization with SSO
enabled whose claimed domains are not all verified loses SSO at those
domains outright: the login page stops sending its users to the identity
provider, and the callback refuses the identity that comes back as outside
the organization's verified domains. That includes organizations whose SSO
worked the day before, so on an instance that already has SSO in use:

1. While verification is still off, have each organization with SSO enabled
   claim and verify its domains in **Organization settings → Single sign-on**.
   Verification works with the setting off; it is just not required yet.
2. Run `manage.py check_sso_domains` until it exits clean. It lists every
   enabled organization's claimed domains with their state and names the
   organizations that would lose SSO if verification were turned on now;
   `--verify` re-checks DNS for every pending claim first.
3. Only then turn it back on using `TRELLUM_SSO_DOMAIN_VERIFICATION`, and
   verify the resulting claims before enforcing SSO.

Verifying needs the organization to publish a TXT record in its own DNS,
which an operator cannot do on its behalf — plan step 1 as a request to each
organization with lead time, not as a same-day configuration change.
