# Make the installing owner Dawarich's administrator before anyone signs in.
#
# The owner's account is created already linked to their Authentik sign-in
# (provider openid_connect, uid = the Authentik user ID Authentik sends as
# "sub"), so their first sign-in lands in it; Dawarich never links by email.
# The demo administrator Dawarich seeds on an empty database is then deleted;
# with a real user present, Dawarich never seeds it again.
require 'json'
require 'securerandom'

owner = JSON.parse(ARGV[0].to_s)
email = owner.fetch('email').to_s.strip.downcase
uid = owner.fetch('owner_uid').to_s
abort 'MU3LAB_ERROR the owner identity is incomplete' if email.empty? || uid.empty?

user = User.unscoped.find_by(provider: 'openid_connect', uid: uid) || User.unscoped.find_by(email: email)
if user.nil?
  user = User.create!(email: email, password: SecureRandom.hex(32), provider: 'openid_connect', uid: uid, admin: true)
elsif user.uid.present? && user.uid != uid
  abort "MU3LAB_ERROR #{email} is already linked to a different sign-in"
else
  user.update!(provider: 'openid_connect', uid: uid, admin: true)
end

# Dawarich deletes accounts its own way: hidden at once, purged later, never able to sign in.
User.where(email: 'demo@dawarich.app').find_each(&:destroy!)
abort 'MU3LAB_ERROR the demo account is still present' if User.exists?(email: 'demo@dawarich.app')
abort 'MU3LAB_ERROR the owner is not an administrator' unless user.reload.admin?
puts 'MU3LAB_DAWARICH_OWNER_OK'
