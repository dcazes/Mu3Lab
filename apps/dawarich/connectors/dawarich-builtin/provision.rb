# Prints the owner's Dawarich API key, for ContextForge to call Dawarich's /api/v1/mcp with.
# The owner is the first administrator (scripts/adopt-owner.rb). Their key is reused, not
# rotated, because their phone may already send locations with it.
owner = User.where(admin: true).order(:created_at).first
abort 'MU3LAB_ERROR Dawarich has no administrator yet' if owner.nil?
owner.update!(api_key: SecureRandom.hex(32)) if owner.api_key.blank?
puts "MU3LAB_OUTPUT api_key=#{owner.api_key}"
