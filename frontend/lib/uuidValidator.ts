/**
 * UUID validation utility
 */

const UUID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Validates if a string is a valid UUID v4
 * @param uuid - String to validate
 * @returns true if valid UUID, false otherwise
 */
export function isValidUUID(uuid: string): boolean {
  if (typeof uuid !== 'string') return false;
  return UUID_REGEX.test(uuid);
}

/**
 * Validates and returns a UUID, or throws an error
 * @param uuid - String to validate
 * @throws Error if not a valid UUID
 * @returns The UUID string if valid
 */
export function validateUUID(uuid: string): string {
  if (!isValidUUID(uuid)) {
    throw new Error(`Invalid UUID format: "${uuid}"`);
  }
  return uuid;
}
