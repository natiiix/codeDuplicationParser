import os
import sys
from .common.repo_cloner import clone_repos
from .common.method_parser import get_methods_from_dir
from .common.args_checker import check_args


def main():
    # verifying inputs.
    # sys.argv should be in the following format:
    # sys.argv = {script name, git_1, git_2}
    flag = check_args(sys.argv)
    if not flag:
        print("    There was an error in your syntax. \n"
              "    Please verify that the git repos exist and your attempted directory to clone into are correct.")
        return

    # Close repositories and get their paths
    repos = clone_repos(sys.argv)

    # ------- FOR TESTING PURPOSES ------------

    # Find all functions and parse their syntax tree using the TreeNode wrapper
    print("Parsing methods in repositories...")
    methods, flat_node_list = get_methods_from_dir(repos[0])

    # Dump all nodes' information into stdout.
    # for node in flat_node_list:
    #     print(node)

    method_count = len(methods)

    for i1, m1 in enumerate(methods):
        for i2 in range(i1 + 1, method_count):
            if methods[i2] == m1:
                print("\n\n" + m1.dump())

    # -----------------------------------------
