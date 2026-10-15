#include "../../src/linux/btop_wsl.hpp"
#include <cassert>
#include <filesystem>
#include <iostream>
#include <unistd.h>

int main() {
	const auto path = std::filesystem::temp_directory_path() / ("btop-snapshot-test-" + std::to_string(getpid()));
	auto write = [&](const std::string& content) { std::ofstream(path) << content; };
	write("version\t1\nupdated\t100\ncpu\tCPU\t80000\t49000\t20\ncore\tP1\t75000\t4100\t40\n"
	      "gpu\tIris Xe\t-1\t300\t0\t786432000\t17000000000\t2\t-1\t0\t-1\t1\n"
	      "disk\tC\tSSD\t35000,49900\t1048576\t0\t3\n");
	auto data = Wsl::read_snapshot(path, 102);
	assert(data.present and data.fresh and data.power == 49000);
	assert(data.cores.at(0).label == "P1" and data.cores.at(0).clock == 4100);
	assert(data.gpus.at(0).temperature == -1 and data.gpus.at(0).shared_memory);
	assert(data.disks.at("C").read == 1048576 and data.disks.at("C").temperatures.size() == 2);
	assert(not Wsl::read_snapshot(path, 111).fresh); // Never present stale hardware data as live.
	assert(not Wsl::read_snapshot(path, 90).fresh); // Reject future timestamps after clock changes.
	write("version\t2\nupdated\t100\n");
	assert(not Wsl::read_snapshot(path, 102).fresh);
	write("version\t1\nupdated\t100\ncore\tP1\tbad\t300\t10\n");
	assert(not Wsl::read_snapshot(path, 102).fresh);
	for (const auto& invalid : {
	    "version\t1\nupdated\t100\ncpu\tCPU\t80000\t49000\t20\ndisk\tX\tSSD\t35000garbage\t1\t2\t3\n",
	    "version\t1\nupdated\t100\ncpu\tCPU\t80000\t49000\t20\ncore\tC1\t75000\t3000\t101\n",
	    "version\t1\nupdated\t100\ncpu\tCPU\t80000\t49000\t20\ncore\tC1\t75000\t3000\t50\ncore\tC1\t75000\t3000\t50\n",
	    "version\t4294967297\nupdated\t100\ncpu\tCPU\t80000\t49000\t20\n",
	    "version\t1\nupdated\t100\n"}) {
		write(invalid); assert(not Wsl::read_snapshot(path, 102).fresh);
	}
	write("version\t1\nupdated\t100\nvalid_for\t3\ncpu_limit\t95000\ncpu\tCPU\t80000\t49000\t20\nthread\t0\t75000\t4100\t10\nthread\t1\t75000\t4100\t30\n");
	data = Wsl::read_snapshot(path, 102);
	assert(data.fresh and data.temperature_max == 95000 and data.threads.size() == 2);
	assert(not Wsl::read_snapshot(path, 104).fresh);
	assert(Wsl::drive_letter("C:\\134") == "C");
	assert(Wsl::drive_letter("w:\\") == "W");
	assert(Wsl::drive_letter("/dev/sdd").empty());
	std::filesystem::remove(path);
	assert(not Wsl::read_snapshot(path, 102).present);
	std::cout << "Snapshot freshness, units, missing metrics and drive recognition passed\n";
}
