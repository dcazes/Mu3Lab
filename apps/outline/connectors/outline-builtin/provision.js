// Prints a new Outline API key for the workspace administrator, for ContextForge to call Outline's /mcp with.
// Earlier Mu3Lab keys are deleted first, so only one is ever valid.
"use strict";

process.chdir("/opt/outline");
const { sequelize } = require("/opt/outline/build/server/storage/database");
const { ApiKey, Team, User } = require("/opt/outline/build/server/models");
const { TeamPreference } = require("/opt/outline/build/shared/types");

const NAME = "Mu3Lab chat";

async function main() {
  const owner = await User.findOne({ where: { role: "admin", suspendedAt: null }, order: [["createdAt", "ASC"]] });
  if (!owner) {
    throw new Error("Outline has no administrator yet");
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
