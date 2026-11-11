import Foundation

/// The desktop stores local time with no zone ("2026-11-11T13:33:53.123456") and sends UTC with one
/// ("2026-11-11T21:33:53.123456+00:00"). These two functions are the same conversion sync_client.py does.
public enum WireTime {
    private static func formatter(_ format: String, utc: Bool, zone: TimeZone = .current) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = utc ? TimeZone(identifier: "UTC") : zone
        f.dateFormat = format
        return f
    }

    /// "2026-11-11T13:33:53" or with fractional seconds, read as local time.
    public static func parseLocal(_ text: String, zone: TimeZone = .current) -> Date? {
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss"] {
            if let date = formatter(format, utc: false, zone: zone).date(from: text) { return date }
        }
        return nil
    }

    public static func localString(_ date: Date, zone: TimeZone = .current) -> String {
        formatter("yyyy-MM-dd'T'HH:mm:ss.SSSSSS", utc: false, zone: zone).string(from: date)
    }

    /// An ISO time with a zone ("Z" or "+hh:mm"), fractional seconds optional.
    public static func parseWire(_ text: String) -> Date? {
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractional.date(from: text) ?? plain.date(from: text) { return date }
        // Python writes six fractional digits; ISO8601DateFormatter wants three. Trim and retry.
        if let dot = text.firstIndex(of: "."),
           let zoneStart = text[dot...].firstIndex(where: { $0 == "+" || $0 == "-" || $0 == "Z" }) {
            let digits = text[text.index(after: dot)..<zoneStart].prefix(3)
            let trimmed = String(text[..<dot]) + "." + digits + String(text[zoneStart...])
            return fractional.date(from: trimmed)
        }
        return nil
    }

    public static func wireString(_ date: Date) -> String {
        formatter("yyyy-MM-dd'T'HH:mm:ss.SSSSSS'+00:00'", utc: true).string(from: date)
    }
}
