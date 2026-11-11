import Foundation

/// The one shared bullet-point note (quicknote_store.py): table `quicknote`, sync id `note`, a list of strings.
/// The whole note is one record and the newest write wins, so the phone writes the whole list.
public enum QuickNote {
    public static let table = "quicknote"
    public static let syncId = "note"
    public static let maxItems = 500
    public static let maxItemChars = 2000

    public static func clean(_ items: [String]) -> [String] {
        Array(items.prefix(maxItems)).map { String($0.prefix(maxItemChars)) }
    }

    public static func items(from record: SyncRecord) -> [String]? {
        guard record.table == table, record.syncId == syncId, let list = record.data.object?["items"]?.array else { return nil }
        let strings = list.compactMap { $0.string }
        return strings.count == list.count ? clean(strings) : nil
    }

    public static func record(items: [String], deviceId: String, now: Date = Date(), offset: TimeInterval = 0) -> SyncRecord {
        SyncRecord(table: table, syncId: syncId,
                   data: .object(["items": .array(clean(items).map { .string($0) })]),
                   deviceId: deviceId, updatedAt: WireTime.wireString(now.addingTimeInterval(offset)), isDeleted: false)
    }
}
