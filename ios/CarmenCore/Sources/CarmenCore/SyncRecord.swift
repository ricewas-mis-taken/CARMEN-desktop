import Foundation

/// One row of the cloud's sync_records table (sync_server/schema.sql).
public struct SyncRecord: Codable, Equatable {
    public var table: String
    public var syncId: String
    public var data: JSONValue
    public var deviceId: String
    public var updatedAt: String
    public var isDeleted: Bool

    public init(table: String, syncId: String, data: JSONValue, deviceId: String, updatedAt: String, isDeleted: Bool) {
        self.table = table; self.syncId = syncId; self.data = data
        self.deviceId = deviceId; self.updatedAt = updatedAt; self.isDeleted = isDeleted
    }

    enum CodingKeys: String, CodingKey {
        case table = "table_name", syncId = "sync_id", data, deviceId = "device_id"
        case updatedAt = "updated_at", isDeleted = "is_deleted"
    }
}
