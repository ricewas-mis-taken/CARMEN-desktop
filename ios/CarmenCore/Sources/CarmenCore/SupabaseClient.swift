import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct AuthSession: Codable, Equatable {
    public var accessToken: String
    public var refreshToken: String
    public var expiresAt: Date
    public var userId: String
    public var email: String
}

public enum CloudError: Error, Equatable {
    case notConfigured
    case badCredentials
    case rejected(Int)
    case network(String)
    case malformed
}

/// Talks straight to Supabase the way sync_cloud.py does: the user's own token plus the publishable key, so
/// row-level security keeps one account away from another's rows.
public final class SupabaseClient {
    public let baseURL: URL
    public let key: String
    private let session: URLSession

    public init(baseURL: URL, key: String, session: URLSession = .shared) {
        self.baseURL = baseURL; self.key = key; self.session = session
    }

    private func request(_ path: String, query: [URLQueryItem] = [], method: String = "GET",
                         token: String? = nil, body: Data? = nil, extra: [String: String] = [:]) throws -> URLRequest {
        var parts = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { parts.queryItems = query }
        guard let url = parts.url else { throw CloudError.malformed }
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.setValue(key, forHTTPHeaderField: "apikey")
        r.setValue("Bearer \(token ?? key)", forHTTPHeaderField: "Authorization")
        if body != nil { r.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        for (k, v) in extra { r.setValue(v, forHTTPHeaderField: k) }
        r.httpBody = body
        r.timeoutInterval = 20
        return r
    }

    private func send(_ r: URLRequest) async throws -> (Data, HTTPURLResponse) {
        do {
            let (data, response) = try await session.data(for: r)
            guard let http = response as? HTTPURLResponse else { throw CloudError.malformed }
            return (data, http)
        } catch let error as CloudError {
            throw error
        } catch {
            throw CloudError.network(error.localizedDescription)
        }
    }

    // MARK: sign in

    private struct TokenReply: Decodable {
        struct User: Decodable { let id: String; let email: String? }
        let access_token: String
        let refresh_token: String
        let expires_in: Double
        let user: User
    }

    private func token(grant: String, body: [String: String]) async throws -> AuthSession {
        let data = try JSONSerialization.data(withJSONObject: body)
        let (reply, http) = try await send(request("auth/v1/token", query: [URLQueryItem(name: "grant_type", value: grant)],
                                                   method: "POST", body: data))
        if http.statusCode == 400 || http.statusCode == 401 { throw CloudError.badCredentials }
        guard (200..<300).contains(http.statusCode) else { throw CloudError.rejected(http.statusCode) }
        guard let parsed = try? JSONDecoder().decode(TokenReply.self, from: reply) else { throw CloudError.malformed }
        return AuthSession(accessToken: parsed.access_token, refreshToken: parsed.refresh_token,
                           expiresAt: Date().addingTimeInterval(parsed.expires_in - 30),
                           userId: parsed.user.id, email: parsed.user.email ?? "")
    }

    public func signIn(email: String, password: String) async throws -> AuthSession {
        try await token(grant: "password", body: ["email": email, "password": password])
    }

    public func refresh(_ old: AuthSession) async throws -> AuthSession {
        try await token(grant: "refresh_token", body: ["refresh_token": old.refreshToken])
    }

    // MARK: sync_records

    private static let fields = "table_name,sync_id,data,device_id,updated_at,is_deleted,server_updated_at"
    static let pageSize = 1000
    static let cursorOverlap: TimeInterval = 30

    public struct Page { public var records: [SyncRecord]; public var cursor: String? }

    private struct Row: Decodable {
        let table_name: String, sync_id: String, data: JSONValue, device_id: String
        let updated_at: String, is_deleted: Bool, server_updated_at: String?
    }

    /// Rows changed since `cursor` (the largest server_updated_at seen before), for the named tables.
    public func pull(tables: [String], cursor: String?, token: String) async throws -> Page {
        var records: [SyncRecord] = []
        var newest = cursor
        var offset = 0
        while true {
            var query = [
                URLQueryItem(name: "select", value: Self.fields),
                URLQueryItem(name: "order", value: "server_updated_at.asc,sync_id.asc"),
                URLQueryItem(name: "limit", value: String(Self.pageSize)),
                URLQueryItem(name: "offset", value: String(offset)),
                URLQueryItem(name: "table_name", value: "in.(\(tables.joined(separator: ",")))"),
            ]
            if let cursor, let when = WireTime.parseWire(cursor) {
                query.append(URLQueryItem(name: "server_updated_at",
                                          value: "gt.\(WireTime.wireString(when.addingTimeInterval(-Self.cursorOverlap)))"))
            }
            let (data, http) = try await send(request("rest/v1/sync_records", query: query, token: token))
            guard (200..<300).contains(http.statusCode) else { throw CloudError.rejected(http.statusCode) }
            guard let rows = try? JSONDecoder().decode([Row].self, from: data) else { throw CloudError.malformed }
            for row in rows {
                records.append(SyncRecord(table: row.table_name, syncId: row.sync_id, data: row.data,
                                         deviceId: row.device_id, updatedAt: row.updated_at, isDeleted: row.is_deleted))
                if let stamp = row.server_updated_at, newest == nil || stamp > newest! { newest = stamp }
            }
            if rows.count < Self.pageSize { break }
            offset += rows.count
        }
        return Page(records: records, cursor: newest)
    }

    /// Upserts records; the database keeps only the newer write of each (migration 001's trigger).
    public func push(_ records: [SyncRecord], userId: String, token: String) async throws {
        guard !records.isEmpty else { return }
        for start in stride(from: 0, to: records.count, by: 200) {
            let batch = records[start..<min(start + 200, records.count)]
            let payload = try batch.map { record -> [String: Any] in
                let data = try JSONEncoder().encode(record.data)
                return [
                    "user_id": userId, "table_name": record.table, "sync_id": record.syncId,
                    "data": try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]),
                    "device_id": record.deviceId, "updated_at": record.updatedAt, "is_deleted": record.isDeleted,
                ]
            }
            let body = try JSONSerialization.data(withJSONObject: payload)
            let (_, http) = try await send(request(
                "rest/v1/sync_records",
                query: [URLQueryItem(name: "on_conflict", value: "user_id,table_name,sync_id")],
                method: "POST", token: token, body: body,
                extra: ["Prefer": "resolution=merge-duplicates,return=minimal"]))
            guard (200..<300).contains(http.statusCode) else { throw CloudError.rejected(http.statusCode) }
        }
    }
}
