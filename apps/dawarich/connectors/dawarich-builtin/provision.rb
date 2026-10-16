# Prints the owner's Dawarich API key, for ContextForge to call Dawarich's /api/v1/mcp with.
# The owner is found by the email Mu3Lab recorded at install (scripts/adopt-owner.rb links it).
# Their key is reused, not rotated, because their phone may already send locations with it.
email = ARGV[0].to_s.strip.downcase
abort "MU3LAB_ERROR the owner's email is missing" if email.empty?
owner = User.find_by(email: email)
abort 'MU3LAB_ERROR the owner has no Dawarich account yet' if owner.nil?
owner.update!(api_key: SecureRandom.hex(32)) if owner.api_key.blank?
puts "MU3LAB_OUTPUT api_key=#{owner.api_key}"
