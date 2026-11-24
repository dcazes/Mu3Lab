// Create Mu3Lab's own LibreChat administrator, which adds everyone's app assistants.
//
// Made through LibreChat's own account service (the one its create-user script uses),
// before anyone signs in: as the first account it would be the administrator anyway,
// and it is made one either way. Its password is Mu3Lab's generated LIBRECHAT_ADMIN_PASSWORD
// (MU3LAB_ADMIN_PASSWORD in the container); the route blocks password sign-in on the
// private address, so only Mu3Lab, on this server, signs in with it.
const path = require('path');
process.chdir('/app');
const bcrypt = require('bcryptjs');
const mongoose = require('mongoose');
require('module-alias')({ base: path.resolve('/app/api') });
const { createPasswordResetUpdate } = require('@librechat/api');
const { createModels, createMethods } = require('@librechat/data-schemas');
const { User, Session } = createModels(mongoose);
const getLogStores = require('~/cache/getLogStores');
const { registerUser } = require('~/server/services/AuthService');
const { connectDb } = require('~/db/connect');

const EMAIL = 'mu3lab@librechat.example.com';

async function main() {
  const password = process.env.MU3LAB_ADMIN_PASSWORD || '';
  if (password.length < 16) throw new Error("Mu3Lab's administrator password is missing");
  await connectDb();
  const methods = createMethods(mongoose, { getCache: getLogStores });
  let user = await User.findOne({ email: EMAIL });
  if (!user) {
    const result = await registerUser(
      { email: EMAIL, password, confirm_password: password, name: 'Mu3Lab', username: 'mu3lab' },
      { emailVerified: true, role: 'ADMIN' },
    );
    if (result.status !== 200) throw new Error(result.message || 'LibreChat did not create the account');
    user = await User.findOne({ email: EMAIL });
    if (!user) throw new Error('LibreChat did not create the account');
  }
  const id = user._id.toString();
  // A reinstall that kept LibreChat's data but made a new password: set it again.
  if (!user.password || !(await bcrypt.compare(password, user.password))) {
    const hashed = await bcrypt.hash(password, await bcrypt.genSalt(10));
    await methods.updateUser(id, createPasswordResetUpdate(hashed), {}, { preserveExpiresAt: true });
    await Session.deleteMany({ user: user._id });
  }
  if (user.role !== 'ADMIN') {
    await methods.updateUser(id, { role: 'ADMIN' }, {}, { preserveExpiresAt: true });
  }
  console.log('MU3LAB_LIBRECHAT_ADMIN_OK');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.log('MU3LAB_ERROR ' + String((error && error.message) || error).replace(/\n/g, ' '));
    process.exit(1);
  });
