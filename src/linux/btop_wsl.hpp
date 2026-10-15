// Windows host sensor snapshot, published atomically by wsl-hwmon-bridge.py.
// No Windows process or network operation runs in btop's rendering thread.
#pragma once
#include <algorithm>
#include <cctype>
#include <ctime>
#include <cstdlib>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace Wsl {
struct Core {
	std::string label;
	long long temperature = -1, clock = -1, load = -1;
};
struct Gpu {
	std::string name;
	long long temperature = -1, clock = -1, power = -1, memory_used = -1,
		memory_total = -1, load = -1, encoder = -1, decoder = -1, memory_clock = -1;
	bool shared_memory = false;
	std::string id = {};
};
struct Disk {
	std::string name;
	std::vector<long long> temperatures;
	long long read = -1, write = -1, activity = -1;
};
struct Snapshot {
	bool present = false, fresh = false;
	long long updated = 0, temperature = -1, power = -1, load = -1, temperature_max = -1;
	std::vector<Core> cores, threads;
	std::vector<Gpu> gpus;
	std::unordered_map<std::string, Disk> disks;
	std::vector<std::pair<std::string, long long>> extra;
};
struct RuntimeConfig {
	std::string state = "/var/lib/wsl-hwmon-state";
	double stale_after = 10;
};
inline std::string trimmed(std::string value) {
	const auto first = value.find_first_not_of(" \t\r");
	return first == std::string::npos ? "" : value.substr(first, value.find_last_not_of(" \t\r") - first + 1);
}
inline double parse_duration(const std::string& text) {
	size_t end = 0;
	const auto value = std::stod(text, &end);
	if (end != text.size() or not std::isfinite(value) or value < 1 or value > 86400)
		throw std::invalid_argument("invalid sensor expiry");
	return value;
}
inline RuntimeConfig runtime_config() {
	RuntimeConfig config;
	const char* path = std::getenv("BTOP4WSL_CONFIG");
	std::ifstream file(path and *path ? path : "/etc/btop4wsl.conf");
	std::string section;
	for (std::string line; std::getline(file, line);) {
		line = trimmed(line);
		if (line.empty() or line[0] == '#' or line[0] == ';') continue;
		if (line.front() == '[' and line.back() == ']') { section = line.substr(1, line.size()-2); continue; }
		const auto equal = line.find('=');
		if (section != "bridge" or equal == std::string::npos) continue;
		const auto key = trimmed(line.substr(0, equal)), value = trimmed(line.substr(equal+1));
		if (key == "state_dir" and not value.empty()) config.state = value;
		else if (key == "stale_after") config.stale_after = parse_duration(value);
	}
	if (const char* value = std::getenv("BTOP4WSL_STATE_DIR"); value and *value) config.state = value;
	if (const char* value = std::getenv("BTOP4WSL_STALE_AFTER"); value and *value) config.stale_after = parse_duration(value);
	if (not std::isfinite(config.stale_after) or config.stale_after < 1 or config.stale_after > 86400)
		throw std::invalid_argument("invalid sensor expiry");
	return config;
}
inline Snapshot read_snapshot(const std::string& path = "", long long now = std::time(nullptr)) {
	Snapshot result;
	try {
		const auto config = runtime_config();
		const char* override_path = std::getenv("BTOP4WSL_SNAPSHOT");
		if (not override_path or not *override_path) override_path = std::getenv("BTOP_WSL_SNAPSHOT");
		const auto filename = not path.empty() ? std::filesystem::path(path) :
			override_path and *override_path ? std::filesystem::path(override_path) : std::filesystem::path(config.state) / "btop.tsv";
		std::ifstream file(filename);
		if (not file) return result;
		result.present = true;
		std::error_code error;
		if (std::filesystem::file_size(filename, error) > 1024 * 1024 or error) return result;
		long long version = 0;
		double valid_for = config.stale_after;
		bool seen_cpu = false, seen_updated = false, seen_version = false;
		std::unordered_set<std::string> core_labels, thread_labels;
		std::string line;
		int lines = 0;
		while (std::getline(file, line)) {
			if (++lines > 1024 or line.size() > 4096) throw std::invalid_argument("oversized snapshot");
			for (const unsigned char byte : line)
				if ((byte < 32 and byte != '\t') or byte == 127) throw std::invalid_argument("control character");
			std::vector<std::string> f;
			std::istringstream row(line);
			for (std::string field; std::getline(row, field, '\t'); ) f.push_back(field);
			if (f.empty()) continue;
			auto parse_num = [&](const std::string& text, long long maximum = 1'000'000'000'000'000ll) {
				size_t end = 0;
				auto value = std::stoll(text, &end);
				if (end != text.size() or value < -1 or value > maximum)
					throw std::invalid_argument("invalid snapshot number");
				return value;
			};
			auto num = [&](size_t index) { return parse_num(f.at(index)); };
			auto require = [&](bool condition) { if (not condition) throw std::invalid_argument("invalid snapshot record"); };
			if (f[0] == "version") { require(f.size() == 2 and not seen_version); version = num(1); seen_version = true; }
			else if (f[0] == "updated") { require(f.size() == 2 and not seen_updated); result.updated = num(1); seen_updated = true; }
			else if (f[0] == "valid_for") { require(f.size() == 2); valid_for = parse_duration(f[1]); }
			else if (f[0] == "cpu_limit") { require(f.size() == 2); result.temperature_max = parse_num(f[1], 250000); }
			else if (f[0] == "cpu") {
				require(f.size() == 5 and not seen_cpu); seen_cpu = true;
				result.temperature = num(2); result.power = num(3); result.load = num(4);
				require(result.temperature <= 250000 and result.load <= 100);
			}
			else if (f[0] == "core" or f[0] == "thread") {
				auto& values = f[0] == "core" ? result.cores : result.threads;
				auto& labels = f[0] == "core" ? core_labels : thread_labels;
				require(f.size() == 5 and values.size() < 4096 and not f[1].empty() and f[1].size() <= 16 and labels.insert(f[1]).second);
				Core core{f[1], num(2), num(3), num(4)};
				require(core.temperature <= 250000 and core.clock <= 1'000'000 and core.load <= 100);
				values.push_back(core);
			}
			else if (f[0] == "gpu") {
				require(f.size() == 12 and result.gpus.size() < 6 and not f[1].empty());
				result.gpus.push_back({f[1], num(2), num(3), num(4), num(5), num(6), num(7), num(8), num(9), num(10), num(11) == 1});
				const auto& gpu = result.gpus.back();
				require(gpu.temperature <= 250000 and gpu.clock <= 1'000'000 and gpu.memory_clock <= 1'000'000
					and gpu.load <= 100 and gpu.encoder <= 100 and gpu.decoder <= 100 and (num(11) == 0 or num(11) == 1));
				result.gpus.back().id = "legacy:" + std::to_string(result.gpus.size()-1) + ":" + f[1];
			}
			else if (f[0] == "gpu_id") {
				require(f.size() == 3 and num(1) >= 0 and static_cast<size_t>(num(1)) < result.gpus.size() and not f[2].empty());
				result.gpus.at(num(1)).id = f[2];
			}
			else if (f[0] == "disk") {
				require(f.size() == 7 and f[1].size() == 1 and std::isupper(static_cast<unsigned char>(f[1][0])) and not result.disks.contains(f[1]));
				Disk disk{f[2], {}, num(4), num(5), num(6)};
				std::istringstream temps(f[3]);
				for (std::string t; std::getline(temps, t, ','); ) { auto temp = parse_num(t, 250000); require(temp >= 0); disk.temperatures.push_back(temp); }
				require(disk.activity <= 100);
				result.disks[f[1]] = std::move(disk);
			}
			else if (f[0] == "extra") { require(f.size() == 3); result.extra.emplace_back(f[1], num(2)); }
		}
		std::unordered_set<std::string> identities;
		for (const auto& gpu : result.gpus)
			if (not identities.insert(gpu.id).second) throw std::invalid_argument("duplicate GPU identity");
		result.fresh = version == 1 and seen_cpu and result.updated > 0 and result.updated <= now + 2
			and now - result.updated <= std::min(valid_for, config.stale_after);
	} catch (const std::exception&) {
		const bool present = result.present;
		result = {}; result.present = present;
	}
	return result;
}
inline std::string drive_letter(const std::string& device) {
	// mount tables escape the trailing backslash, e.g. C:\134.
	if (device.size() >= 2 and std::isalpha(static_cast<unsigned char>(device[0])) and device[1] == ':')
		return std::string(1, std::toupper(static_cast<unsigned char>(device[0])));
	return {};
}
}
