// Deletes the Outline API key Mu3Lab made for chat (see provision.js), when its connector is switched off.
"use strict";

process.chdir("/opt/outline");
const { sequelize } = require("/opt/outline/build/server/storage/database");
const { ApiKey } = require("/opt/outline/build/server/models");

const NAME = "Mu3Lab chat";

async function main() {
  const removed = await ApiKey.destroy({ where: { name: NAME } });
  console.log("MU3LAB_OUTPUT removed=" + removed);
}

main()
  .then(() => sequelize.close())
  .then(() => process.exit(0))
  .catch((error) => {
    console.log("MU3LAB_ERROR " + String((error && error.message) || error).replace(/\n/g, " "));
    process.exit(1);
  });
