# Once the owner has signed in: make them Dawarich's administrator, then
# remove the demo administrator Dawarich seeds on an empty database. With a
# real user present, Dawarich's seed never recreates it.
require 'json'

owner = JSON.parse(ARGV[0].to_s)
email = owner.fetch('email').to_s.strip.downcase
user = User.find_by(email: email)
abort "MU3LAB_ERROR the owner #{owner['username']} has not signed in yet" unless user

user.update!(admin: true) unless user.admin?
demo = User.find_by(email: 'demo@dawarich.app')
demo&.destroy!
abort 'MU3LAB_ERROR the demo account is still present' if User.exists?(email: 'demo@dawarich.app')
puts 'MU3LAB_DAWARICH_OWNER_OK'
