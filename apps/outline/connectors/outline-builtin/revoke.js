// Deletes the Outline API key Mu3Lab made for one person's chat (see provision.js); the email is in argv.
"use strict";

process.chdir("/opt/outline");
const { sequelize } = require("/opt/outline/build/server/storage/database");
const { ApiKey, User } = require("/opt/outline/build/server/models");

const NAME = "Mu3Lab chat";

async function main() {
  const email = String(process.argv[2] || "").trim().toLowerCase();
  const user = email ? await User.findOne({ where: { email } }) : null;
  const removed = user ? await ApiKey.destroy({ where: { name: NAME, userId: user.id } }) : 0;
  console.log("MU3LAB_OUTPUT removed=" + removed);
}

main()
  .then(() => sequelize.close())
  .then(() => process.exit(0))
  .catch((error) => {
    console.log("MU3LAB_ERROR " + String((error && error.message) || error).replace(/\n/g, " "));
    process.exit(1);
  });
