// SPDX-License-Identifier: Apache-2.0

#include "btop.hpp"

#include <iterator>
#include <ranges>
#include <string_view>
#include <vector>

auto main(int argc, const char* argv[]) -> int {
	std::vector<std::string_view> args;
	for (int i = 1; i < argc; ++i) args.emplace_back(argv[i]);
	return btop_main(args);
}
