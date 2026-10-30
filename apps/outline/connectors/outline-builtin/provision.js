// Prints a new Outline API key for one person, found by the email in argv, for ContextForge to call Outline's /mcp with.
// Their earlier Mu3Lab key is deleted first, so only one is ever valid per person.
"use strict";

process.chdir("/opt/outline");
const { sequelize } = require("/opt/outline/build/server/storage/database");
const { ApiKey, Team, User } = require("/opt/outline/build/server/models");
const { TeamPreference } = require("/opt/outline/build/shared/types");

const NAME = "Mu3Lab chat";

async function main() {
  const email = String(process.argv[2] || "").trim().toLowerCase();
  if (!email) {
    throw new Error("the person's email is missing");
  }
  const owner = await User.findOne({ where: { email, suspendedAt: null } });
  if (!owner) {
    throw new Error("Open Outline and sign in once; chat connects after your Outline account exists");
  }
  const team = await Team.findByPk(owner.teamId);
  if (!team.getPreference(TeamPreference.MCP)) {
    team.setPreference(TeamPreference.MCP, true);
    await team.save();
  }
  await ApiKey.destroy({ where: { userId: owner.id, name: NAME } });
  const key = await ApiKey.create({ name: NAME, userId: owner.id });
  console.log("MU3LAB_OUTPUT api_key=" + key.value);
}

main()
  .then(() => sequelize.close())
  .then(() => process.exit(0))
  .catch((error) => {
    console.log("MU3LAB_ERROR " + String((error && error.message) || error).replace(/\n/g, " "));
    process.exit(1);
  });
