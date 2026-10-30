# Prints one person's Dawarich API key, found by email, for ContextForge to call Dawarich's /api/v1/mcp with.
# Their key is reused, not rotated, because their phone may already send locations with it.
email = ARGV[0].to_s.strip.downcase
abort "MU3LAB_ERROR the person's email is missing" if email.empty?
owner = User.find_by(email: email)
abort 'MU3LAB_ERROR Open Dawarich and sign in once; chat connects after your account exists' if owner.nil?
owner.update!(api_key: SecureRandom.hex(32)) if owner.api_key.blank?
puts "MU3LAB_OUTPUT api_key=#{owner.api_key}"
