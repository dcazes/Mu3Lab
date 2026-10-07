// Create Outline's workspace and the installing owner's administrator before anyone signs in.
//
// This calls accountProvisioner, the same Outline command its OIDC sign-in runs on a
// first sign-in, with the owner's details: the workspace is created, the owner is its
// administrator, and their account is already linked to their Authentik sign-in
// (authentication provider = Authentik's host, provider ID = the Authentik user ID
// Authentik sends as "sub"). Run again, it finds the same account and changes nothing.
"use strict";

process.chdir("/opt/outline");
const env = require("/opt/outline/build/server/env").default;
const { sequelize } = require("/opt/outline/build/server/storage/database");
const { createContext } = require("/opt/outline/build/server/context");
const accountProvisioner = require("/opt/outline/build/server/commands/accountProvisioner").default;
const { User } = require("/opt/outline/build/server/models");

async function main() {
  const owner = JSON.parse(process.argv[2] || "{}");
  const authentikHost = process.argv[3] || "";
  const email = String(owner.email || "").trim().toLowerCase();
  const sub = String(owner.owner_uid || "").trim();
  if (!email || !sub || !authentikHost) {
    throw new Error("the owner identity is incomplete");
  }
  const domain = email.split("@")[1];
  const result = await accountProvisioner(createContext({ ip: "127.0.0.1" }), {
    team: { name: env.APP_NAME, domain, subdomain: domain.split(".")[0] },
    user: { name: owner.display_name || owner.username, email, emailVerified: true, language: "en_US" },
    authenticationProvider: { name: "oidc", providerId: authentikHost },
    authentication: { providerId: sub, scopes: ["openid", "profile", "email"] },
  });
  const user = await User.findByPk(result.user.id);
  if (!user || user.role !== "admin") {
    throw new Error("the owner is not Outline's administrator");
  }
  console.log("MU3LAB_OUTLINE_OWNER_OK");
}

main()
  .then(() => sequelize.close())
  .then(() => process.exit(0))
  .catch((error) => {
    console.log("MU3LAB_ERROR " + String((error && error.message) || error).replace(/\n/g, " "));
    process.exit(1);
  });
