/**
 * 12306 车票查询 — C++ 交互界面
 * 仅负责：用户输入 + 调用 Python 引擎查询 + setw() 表格展示
 * 编译: g++ -std=c++17 -O2 -static display.cpp -o display.exe
 */

#include <algorithm>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

#include "json.hpp"

using json = nlohmann::json;

// ==================================================================
// 路径工具
// ==================================================================
bool fileExists(const std::string& path) {
    std::ifstream f(path); return f.good();
}
std::string exeDir() {
#ifdef _WIN32
    char buf[MAX_PATH]; GetModuleFileNameA(NULL, buf, MAX_PATH);
    std::string p(buf); auto pos = p.rfind('\\');
    return (pos != std::string::npos) ? p.substr(0, pos) : ".";
#else
    return ".";
#endif
}
std::string todayStr() {
    time_t t = time(nullptr); char buf[16];
    strftime(buf, sizeof(buf), "%Y-%m-%d", localtime(&t));
    return buf;
}
std::string trim(const std::string& s) {
    size_t b = 0, e = s.size();
    while (b < e && (unsigned char)s[b] <= ' ') b++;
    while (e > b && (unsigned char)s[e-1] <= ' ') e--;
    return s.substr(b, e - b);
}
std::string clean(const std::string& s) { auto v = trim(s); return v.empty() ? "--" : v; }

// ==================================================================
// 席位数据（与 Python query_12306.py 一致）
// ==================================================================
std::map<std::string,std::string> seatCN() {
    return {{"SW","商务座"},{"TZ","特等座"},{"Y1","优选一等座"},
            {"ZY","一等座"},{"ZE","二等座"},{"WY","动卧/一等卧"},
            {"WE","二等卧"},{"GR","高级软卧"},{"WR","软卧"},
            {"YW","硬卧"},{"RZ","软座"},{"YZ","硬座"},{"WZ","无座"}};
}
std::vector<std::string> seatOrder() {
    return {"SW","TZ","Y1","ZY","ZE","WY","WE","GR","WR","YW","RZ","YZ","WZ"};
}
std::map<std::string, std::map<int,std::string>> seatOffsets() {
    return {{"G",{{20,"Y1"},{23,"WY"},{25,"TZ"},{30,"ZE"},{31,"ZY"},{32,"SW"}}},
            {"C",{{20,"Y1"},{23,"WY"},{25,"TZ"},{30,"ZE"},{31,"ZY"},{32,"SW"}}},
            {"D",{{20,"Y1"},{23,"WY"},{25,"TZ"},{28,"WE"},{30,"ZE"},{31,"ZY"},{32,"SW"}}},
            {"_",{{21,"GR"},{23,"WR"},{26,"WZ"},{28,"YW"},{29,"YZ"}}}};
}
std::string fmtVal(const std::string& v) {
    if(v=="有") return "有"; if(v=="候补") return "候补";
    if(v=="无") return "无票"; if(v=="*"||v=="--") return "--"; return v;
}

// ==================================================================
// 结果解析
// ==================================================================
struct Train {
    std::string code, from, to, dep, arr, dur;
    std::map<std::string,std::string> seats;
};
std::vector<std::string> split(const std::string& s, char delim) {
    std::vector<std::string> p; std::stringstream ss(s); std::string item;
    while(std::getline(ss,item,delim)) p.push_back(item); return p;
}
std::map<int,std::string> getOffsets(const std::string& code) {
    static auto tbl=seatOffsets();
    std::string pfx="_";
    if(!code.empty()&&std::isalpha((unsigned char)code[0])) pfx=std::string(1,code[0]);
    auto it=tbl.find(pfx); return it!=tbl.end()?it->second:tbl["_"];
}
Train parseOne(const std::string& raw, const std::map<std::string,std::string>& sn) {
    auto p=split(raw,'|'); Train t;
    t.code=clean(p.size()>3?p[3]:""); t.dep=clean(p.size()>8?p[8]:"");
    t.arr=clean(p.size()>9?p[9]:""); t.dur=clean(p.size()>10?p[10]:"");
    auto lk=[&](const std::string& c){auto it=sn.find(clean(c));return it!=sn.end()?it->second:clean(c);};
    t.from=lk(p.size()>6?p[6]:""); t.to=lk(p.size()>7?p[7]:"");
    for(auto&[idx,code]:getOffsets(t.code))
        t.seats[code]=idx<(int)p.size()?clean(p[idx]):"--";
    return t;
}

// ==================================================================
// 表格输出
// ==================================================================
void printCell(int w, const std::string& s) {
    int cn=0;
    for(size_t i=0;i<s.size();)
        if((unsigned char)s[i]>=0xE0&&(unsigned char)s[i]<=0xEF){i+=3;cn++;}else i++;
    int pad=w-((int)s.size()-cn*2)-cn; if(pad<1)pad=1;
    std::cout<<s<<std::string(pad,' ');
}

void displayFile(const std::string& path) {
    std::ifstream f(path);
    if(!f.is_open()){std::cerr<<"无法打开: "<<path<<"\n";return;}
    json data; try{f>>data;}catch(...){std::cerr<<"JSON解析失败\n";return;}

    auto scn=seatCN(); auto sdo=seatOrder();
    std::map<std::string,std::string> sn;
    for(auto&[k,v]:data["data"]["map"].items()) sn[k]=v.get<std::string>();

    std::vector<Train> trains; std::set<std::string> seen;
    for(auto& r:data["data"]["result"]){
        Train t=parseOne(r.get<std::string>(),sn);
        if(t.code.empty()||t.code=="--") continue;
        trains.push_back(t);
        for(auto&[c,_]:t.seats) seen.insert(c);
    }
    // 只展示有数据的席位列
    std::vector<std::string> cols;
    for(auto& c:sdo) if(seen.count(c)){
        bool any=false;
        for(auto& t:trains) if(t.seats.count(c)&&t.seats[c]!="--"){any=true;break;}
        if(any) cols.push_back(c);
    }
    const int WT=8,WF=10,WO=10,WD=6,WA=6,WU=8,WS=7;
    printCell(WT,"车次");printCell(WF,"发站");printCell(WO,"到站");
    printCell(WD,"发时");printCell(WA,"到时");printCell(WU,"历时");
    for(auto& c:cols){auto it=scn.find(c);printCell(WS,it!=scn.end()?it->second:c);}
    std::cout<<"\n"<<std::string(WT+WF+WO+WD+WA+WU+(int)cols.size()*WS,'=')<<"\n";
    for(auto& t:trains){
        printCell(WT,t.code);printCell(WF,t.from);printCell(WO,t.to);
        printCell(WD,t.dep);printCell(WA,t.arr);printCell(WU,t.dur);
        for(auto& c:cols){auto it=t.seats.find(c);printCell(WS,it!=t.seats.end()?fmtVal(it->second):"--");}
        std::cout<<"\n";
    }
    std::cout<<"\n  共 "<<trains.size()<<" 趟列车\n\n";
}

// ==================================================================
// 引擎调用（CreateProcess，避免 system() 的 cmd 解析问题）
// ==================================================================
std::string findEngine() {
    std::string e = exeDir() + "\\query_engine.exe";
    if(!fileExists(e)) e = exeDir() + "/query_engine.exe";
    if(fileExists(e)) return e;
    return "python";  // fallback: need script too
}

bool runEngine(const std::string& date, const std::string& from,
               const std::string& to, const std::string& outFile) {
#ifdef _WIN32
    std::string engine = findEngine();
    std::string cmdLine;
    if(engine == "python") {
        cmdLine = "python main.py query " + date + " \"" + from + "\" \"" +
                  to + "\" -o \"" + outFile + "\"";
    } else {
        cmdLine = "\"" + engine + "\" query " + date + " \"" + from + "\" \"" +
                  to + "\" -o \"" + outFile + "\"";
    }

    STARTUPINFOA si = {sizeof(si)};
    PROCESS_INFORMATION pi = {};
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;

    // 需要可修改的缓冲区
    std::vector<char> buf(cmdLine.begin(), cmdLine.end());
    buf.push_back('\0');

    BOOL ok = CreateProcessA(NULL, buf.data(), NULL, NULL, FALSE,
                             CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
    if(!ok) return false;
    WaitForSingleObject(pi.hProcess, 30000);  // 最多等30秒
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return fileExists(outFile);
#else
    std::string cmd = "python3 main.py query " + date + " \"" + from + "\" \"" +
                      to + "\" -o \"" + outFile + "\"";
    return system(cmd.c_str()) == 0;
#endif
}

// ==================================================================
// 主入口
// ==================================================================
const std::string EXE_DIR = exeDir();
std::string g_engine;

int main() {
#ifdef _WIN32
    system("chcp 65001 > nul");
#endif

    std::cout<<"\n  ╔══════════════════════════════╗\n"
               "  ║     12306 车票查询工具      ║\n"
               "  ╚══════════════════════════════╝\n\n"
               "  随时输入 q 可退出\n\n";

    CreateDirectoryA((EXE_DIR + "\\results").c_str(), NULL);
    std::string today = todayStr();

    while(true) {
        // 日期
        std::string date;
        std::cout<<"  乘车日期 ["<<today<<"]: "<<std::flush;
        std::getline(std::cin, date); date=trim(date);
        if(date=="q"||date=="Q") break;
        if(date.empty()) date=today;

        // 始发站
        std::string from;
        std::cout<<"  始发城市/站名: "<<std::flush;
        std::getline(std::cin, from); from=trim(from);
        if(from=="q"||from=="Q") break;
        if(from.empty()){std::cout<<"  不能为空\n";continue;}

        // 终到站
        std::string to;
        std::cout<<"  终到城市/站名: "<<std::flush;
        std::getline(std::cin, to); to=trim(to);
        if(to=="q"||to=="Q") break;
        if(to.empty()){std::cout<<"  不能为空\n";continue;}

        // 确认
        std::cout<<"\n  日期: "<<date<<"\n  行程: "<<from<<" → "<<to<<"\n"
                    "  确认查询? [Y/n/q]: "<<std::flush;
        std::string yn; std::getline(std::cin, yn); yn=trim(yn);
        if(yn=="q"||yn=="Q") break;
        if(!yn.empty()&&yn!="y"&&yn!="Y"&&yn!="yes"){std::cout<<"  已取消\n\n";continue;}

        // 调用 Python 引擎
        std::string outFile = EXE_DIR + "\\results\\" + from + "_" + to + "_" + date + ".json";
        std::cout<<"\n  正在查询 12306..."<<std::flush;
        if(!runEngine(date, from, to, outFile))
            std::cout<<"\n  [错误] 查询失败\n";
        else {std::cout<<"完成\n"; displayFile(outFile);}

        std::cout<<"  继续查询? [Y/q]: "<<std::flush;
        std::getline(std::cin, yn); yn=trim(yn);
        if(yn=="q"||yn=="Q") break;
        std::cout<<"\n";
    }
    std::cout<<"\n  按回车键退出..."<<std::flush; std::cin.get();
    return 0;
}
