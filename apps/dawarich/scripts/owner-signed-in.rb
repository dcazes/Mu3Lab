# Read-only: has the installing owner signed in through Authentik yet?
# ARGV[0] is the owner's Authentik username; Dawarich keeps people by email,
# so the Mu3Lab owner record's email arrives as ARGV[1] when known.
email = ARGV[1].to_s.strip.downcase
found = email.empty? ? User.where.not(email: 'demo@dawarich.app').exists? : User.exists?(email: email)
puts 'MU3LAB_DAWARICH_OWNER_OK' if found
